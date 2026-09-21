"""Local model backend: Ollama for generation, a search backend for retrieval.

Nothing leaves the machine except the web searches and the page fetches, which
carry only the search terms - never the Confluence content, never the AS-IS
process, never the analysis.

The division of labour differs from the hosted path. Claude's server-side web
search lets the model choose and run its own queries; Ollama has no such tool,
so retrieval happens here in code:

    research()  = run the stage's queries -> filter by domain -> fetch pages
                  (no model call at all)
    extract()   = one constrained-JSON model call over what was retrieved

That is arguably the more auditable arrangement: the queries are the ones the
pipeline built, and the model only ever sees text that was actually retrieved.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Iterator, Sequence, Type, TypeVar

import requests
from pydantic import BaseModel, ValidationError

from ..config import Settings
from ..net import build_session, is_tls_trust_error
from ..research.http import fetch_text
from ..research.transcript import ResearchTranscript, SearchHit
from ..search.base import SearchBackend
from .schema import flatten_schema

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class OllamaUnavailableError(RuntimeError):
    pass


class OllamaClient:
    offline = False

    def __init__(self, settings: Settings, search: SearchBackend) -> None:
        self.settings = settings
        self.search = search
        self.host = settings.ollama_host.rstrip("/")
        self.model = settings.ollama_model
        self.session = build_session(settings)      # for page fetches, not for Ollama itself
        self._warm = False

    # ------------------------------------------------------------------
    # health
    # ------------------------------------------------------------------
    def available_models(self) -> list[str]:
        try:
            response = requests.get(f"{self.host}/api/tags", timeout=10)
            response.raise_for_status()
        except Exception as exc:
            raise OllamaUnavailableError(
                f"Could not reach Ollama at {self.host} ({exc}). Is `ollama serve` running?"
            ) from exc
        return [model.get("name", "") for model in response.json().get("models", [])]

    def check(self) -> None:
        models = self.available_models()
        base = self.model.split(":")[0]
        if not any(name == self.model or name.split(":")[0] == base for name in models):
            raise OllamaUnavailableError(
                f"Ollama is running but '{self.model}' is not pulled. Run `ollama pull {self.model}`. "
                f"Available: {', '.join(models) or 'none'}"
            )

    # ------------------------------------------------------------------
    # research: retrieval only, no model call
    # ------------------------------------------------------------------
    def research(
        self,
        *,
        system: str,
        prompt: str,
        queries: Sequence[str] | None = None,
        allowed_domains: Sequence[str] | None = None,
        blocked_domains: Sequence[str] | None = None,
        max_searches: int = 10,
    ) -> ResearchTranscript:
        planned = [query for query in (queries or []) if query][:max_searches]
        if not planned:
            logger.warning("No queries supplied for a local research stage - nothing to retrieve.")
            return ResearchTranscript(stop_reason="no_queries")

        transcript = ResearchTranscript(queries=planned)
        seen: set[str] = set()
        for query in planned:
            for result in self.search.search(
                query,
                max_results=self.settings.search_results_per_query,
                allowed_domains=allowed_domains,
            ):
                if result.url in seen:
                    continue
                seen.add(result.url)
                transcript.hits.append(
                    SearchHit(url=result.url, title=result.title, query=query, snippet=result.snippet)
                )

        # Fetch the most promising pages. Page text is what the extraction call
        # reads, and what quote verification later checks claims against.
        budget = self.settings.local_max_pages
        excerpts: list[str] = []
        for hit in transcript.hits[:budget]:
            page = fetch_text(hit.url, self.settings.cache_dir, self.settings.http_timeout, self.session)
            if not page:
                continue
            excerpt = page[: self.settings.local_page_chars]
            transcript.pages[hit.url] = page
            excerpts.append(f"### SOURCE: {hit.title or hit.url}\nURL: {hit.url}\n\n{excerpt}\n")

        if not excerpts:
            logger.warning("Local research retrieved %d hits but no readable pages.", len(transcript.hits))
        transcript.text = "\n".join(excerpts)
        transcript.stop_reason = "end_turn"
        return transcript

    # ------------------------------------------------------------------
    # extract: one constrained-JSON call
    # ------------------------------------------------------------------
    def extract(self, *, system: str, prompt: str, schema: Type[T]) -> T:
        schema_json = flatten_schema(schema.model_json_schema())
        budget = self.settings.local_prompt_chars
        if len(prompt) > budget:
            logger.info("Trimming the extraction prompt from %d to %d characters for the local model.",
                        len(prompt), budget)
            prompt = prompt[:budget] + "\n\n[content truncated to fit the local model's context]"

        messages: list[dict[str, str]] = [
            {"role": "system", "content": system + self._compact_rule()},
            {"role": "user", "content": prompt},
        ]

        last_error = ""
        for attempt in range(1, self.settings.ollama_max_attempts + 1):
            content = self._chat(messages, schema_json)
            try:
                return schema.model_validate_json(content)
            except ValidationError as exc:
                last_error = str(exc)[:1200]
                logger.warning("Local model returned JSON that does not fit %s (attempt %d/%d).",
                               schema.__name__, attempt, self.settings.ollama_max_attempts)
            except json.JSONDecodeError as exc:
                last_error = f"invalid JSON: {exc}"
                logger.warning("Local model returned invalid JSON (attempt %d/%d).",
                               attempt, self.settings.ollama_max_attempts)
            messages = messages[:2] + [
                {"role": "assistant", "content": content[:2000]},
                {
                    "role": "user",
                    "content": (
                        "That response did not satisfy the required schema:\n"
                        f"{last_error}\n\n"
                        "Return corrected JSON only - no prose, no markdown fences. Every required "
                        "field must be present, and fields you have no evidence for must be empty "
                        "rather than invented."
                    ),
                },
            ]

        raise RuntimeError(
            f"The local model '{self.model}' could not produce valid {schema.__name__} JSON after "
            f"{self.settings.ollama_max_attempts} attempts. Last error: {last_error}\n"
            "Try a more capable model (AFSGAP_OLLAMA_MODEL), or raise AFSGAP_OLLAMA_NUM_PREDICT if "
            "the JSON is being cut off mid-structure."
        )

    # ------------------------------------------------------------------
    # model lifecycle
    # ------------------------------------------------------------------
    def warm_up(self) -> None:
        """Load the model before the first real call.

        Otherwise the first stage silently includes however long it takes to
        read several gigabytes off disk, which reads as a hang.
        """
        if self._warm:
            return
        logger.info("Loading %s into memory (first use can take a minute)...", self.model)
        started = time.monotonic()
        try:
            requests.post(
                f"{self.host}/api/generate",
                json={"model": self.model, "prompt": "", "keep_alive": self.settings.ollama_keep_alive},
                timeout=self.settings.ollama_chunk_timeout,
            ).raise_for_status()
        except Exception as exc:
            logger.warning("Could not preload %s (%s); continuing anyway.", self.model, exc)
            return
        self._warm = True
        logger.info("Model ready in %.0fs.", time.monotonic() - started)

    def _compact_rule(self) -> str:
        if not self.settings.local_compact:
            return ""
        limit = self.settings.local_max_items
        return (
            f"\n\nLENGTH BUDGET. Return at most {limit} items in any list, at most one evidence "
            "quote per item, and keep every description to one or two sentences. A short, "
            "well-grounded answer is correct; length is not quality, and a long answer will not "
            "finish in reasonable time on this machine."
        )

    def benchmark(self, tokens: int = 120) -> dict[str, float]:
        """Measure load time and generation speed, to size the settings.

        The numbers that matter are tokens per second and how long the model
        takes to load: a stage generates roughly 800-2000 tokens, so the rate
        tells you directly whether a run is minutes or hours.
        """
        load_started = time.monotonic()
        self.warm_up()
        load_seconds = time.monotonic() - load_started

        started = time.monotonic()
        produced = 0
        with requests.post(
            f"{self.host}/api/chat",
            json={
                "model": self.model,
                "messages": [{"role": "user", "content": "Count from 1 to 200, one number per line."}],
                "stream": True,
                "keep_alive": self.settings.ollama_keep_alive,
                "options": {"temperature": 0, "num_ctx": 2048, "num_predict": tokens},
            },
            stream=True,
            timeout=(10, self.settings.ollama_chunk_timeout),
        ) as response:
            response.raise_for_status()
            for _ in self._iter_content(response):
                produced += 1
        elapsed = max(time.monotonic() - started, 1e-6)
        return {
            "load_seconds": load_seconds,
            "tokens": produced,
            "seconds": elapsed,
            "tokens_per_second": produced / elapsed,
        }

    # ------------------------------------------------------------------
    # streaming chat
    # ------------------------------------------------------------------
    def _chat(self, messages: list[dict[str, str]], schema_json: dict[str, Any]) -> str:
        """Stream one completion, returning the accumulated content.

        Streaming matters for more than progress reporting: the read timeout
        then applies between chunks rather than to the whole call, so a model
        that is slow but working is never killed, while one that has genuinely
        hung still is.
        """
        self.warm_up()
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "format": schema_json,
            "keep_alive": self.settings.ollama_keep_alive,
            "options": {
                "temperature": self.settings.ollama_temperature,
                "num_ctx": self.settings.ollama_num_ctx,
                "num_predict": self.settings.ollama_num_predict,
            },
        }

        started = time.monotonic()
        chunks: list[str] = []
        tokens = 0
        last_report = started

        try:
            with requests.post(
                f"{self.host}/api/chat",
                json=payload,
                stream=True,
                timeout=(10, self.settings.ollama_chunk_timeout),
            ) as response:
                response.raise_for_status()
                for piece in self._iter_content(response):
                    chunks.append(piece)
                    tokens += 1
                    now = time.monotonic()
                    if now - last_report >= 30:
                        rate = tokens / max(now - started, 1e-6)
                        logger.info("  ... %d tokens in %.0fs (%.1f tok/s)", tokens, now - started, rate)
                        last_report = now
                    if self.settings.ollama_timeout and (now - started) > self.settings.ollama_timeout:
                        raise OllamaUnavailableError(
                            f"The local model has been generating for {now - started:.0f}s without "
                            f"finishing (AFSGAP_OLLAMA_TIMEOUT). It is running, just too slowly for "
                            "this input. Run `python -m afsgap doctor --bench` for settings this "
                            "machine can sustain, or use a smaller model."
                        )
        except requests.Timeout as exc:
            raise OllamaUnavailableError(
                f"Ollama produced nothing for {self.settings.ollama_chunk_timeout}s "
                f"(AFSGAP_OLLAMA_CHUNK_TIMEOUT). The model is likely still loading, or the machine "
                "is out of memory. Try a smaller model, or raise that value."
            ) from exc
        except requests.RequestException as exc:
            raise OllamaUnavailableError(f"Ollama request failed: {exc}") from exc

        elapsed = time.monotonic() - started
        logger.info("  generated %d tokens in %.0fs (%.1f tok/s)", tokens, elapsed, tokens / max(elapsed, 1e-6))

        content = "".join(chunks).strip()
        # Some models still wrap JSON in fences despite the format constraint.
        if content.startswith("```"):
            content = content.strip("`")
            content = content.split("\n", 1)[-1] if content.lower().startswith("json") else content
        return content

    @staticmethod
    def _iter_content(response) -> Iterator[str]:
        """Yield message content from Ollama's newline-delimited JSON stream."""
        for line in response.iter_lines(decode_unicode=True):
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("error"):
                raise OllamaUnavailableError(f"Ollama reported an error: {event['error']}")
            piece = (event.get("message") or {}).get("content") or ""
            if piece:
                yield piece
            if event.get("done"):
                break
