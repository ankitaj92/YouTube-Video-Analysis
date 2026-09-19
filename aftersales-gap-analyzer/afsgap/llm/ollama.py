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
from typing import Any, Sequence, Type, TypeVar

import requests
from pydantic import BaseModel, ValidationError

from ..config import Settings
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
            page = fetch_text(hit.url, self.settings.cache_dir, self.settings.http_timeout)
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
            {"role": "system", "content": system},
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
            "Try a larger model (AFSGAP_OLLAMA_MODEL), or raise AFSGAP_OLLAMA_NUM_CTX if the "
            "input is being truncated."
        )

    def _chat(self, messages: list[dict[str, str]], schema_json: dict[str, Any]) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "format": schema_json,
            "options": {
                "temperature": self.settings.ollama_temperature,
                "num_ctx": self.settings.ollama_num_ctx,
            },
        }
        try:
            response = requests.post(
                f"{self.host}/api/chat", json=payload, timeout=self.settings.ollama_timeout
            )
            response.raise_for_status()
        except requests.Timeout as exc:
            raise OllamaUnavailableError(
                f"Ollama timed out after {self.settings.ollama_timeout}s. Local models are slow on "
                "long inputs - raise AFSGAP_OLLAMA_TIMEOUT, or lower AFSGAP_LOCAL_MAX_PAGES."
            ) from exc
        except Exception as exc:
            raise OllamaUnavailableError(f"Ollama request failed: {exc}") from exc

        body = response.json()
        content = ((body.get("message") or {}).get("content") or "").strip()
        # Some models still wrap JSON in fences despite the format constraint.
        if content.startswith("```"):
            content = content.strip("`")
            content = content.split("\n", 1)[-1] if content.lower().startswith("json") else content
        return content
