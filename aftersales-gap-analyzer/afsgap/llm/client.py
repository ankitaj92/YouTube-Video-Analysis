"""Thin, typed wrapper around the Anthropic Messages API.

Two call shapes are used by the pipeline, deliberately kept separate:

``research()``  - server-side ``web_search`` (optionally domain-restricted),
                  free-form text out, and every search result + citation
                  captured so later stages can prove where a claim came from.
``extract()``   - no tools, structured output validated against a Pydantic
                  model. The evidence gathered by ``research()`` is passed in as
                  plain text, so extraction can never invent a new source.

Keeping them apart is what makes "a T-code is only returned when a public SAP
source explicitly supports it" checkable: extraction only sees text that came
back from an allowlisted domain.
"""

from __future__ import annotations

import logging
from typing import Any, Sequence, Type, TypeVar

from pydantic import BaseModel

from ..config import Settings
from ..research.transcript import Citation, ResearchTranscript, SearchHit

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class LlmUnavailableError(RuntimeError):
    pass


class ClaudeClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        try:
            import anthropic  # imported lazily so offline runs need no SDK
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise LlmUnavailableError(
                "The 'anthropic' package is required for live runs. "
                "Install it with `pip install -r requirements.txt`, or run with --offline."
            ) from exc
        self._anthropic = anthropic
        self._client = anthropic.Anthropic()

    # ------------------------------------------------------------------
    # research: web search + citations
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
        # `queries` are already embedded in the prompt for this backend - Claude
        # runs its own searches through the server-side tool. The local backend
        # uses them directly, which is why they are part of the interface.
        tool: dict[str, Any] = {
            "type": self.settings.web_search_tool_type,
            "name": "web_search",
            "max_uses": max_searches,
        }
        if allowed_domains:
            tool["allowed_domains"] = list(allowed_domains)
        elif blocked_domains:
            tool["blocked_domains"] = list(blocked_domains)

        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        transcript = ResearchTranscript()

        # `pause_turn` means the server tool loop was interrupted mid-flight;
        # continue by replaying the assistant turn verbatim.
        for _ in range(6):
            message = self._create_research_message(system=system, messages=messages, tool=tool)
            self._guard_refusal(message)
            self._absorb(message, transcript)
            transcript.stop_reason = getattr(message, "stop_reason", "") or ""
            if transcript.stop_reason != "pause_turn":
                break
            messages.append({"role": "assistant", "content": message.content})
        return transcript

    def _create_research_message(self, *, system: str, messages: list[dict[str, Any]], tool: dict[str, Any]):
        base_kwargs: dict[str, Any] = {
            "model": self.settings.model,
            "max_tokens": self.settings.research_max_tokens,
            "system": system,
            "messages": messages,
            "tools": [tool],
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": self.settings.effort},
        }
        if self.settings.enable_server_side_fallbacks:
            try:
                with self._client.beta.messages.stream(
                    **base_kwargs,
                    betas=[self.settings.fallback_beta],
                    fallbacks="default",
                ) as stream:
                    return stream.get_final_message()
            except (self._anthropic.BadRequestError, TypeError) as exc:
                logger.warning("Server-side fallbacks unavailable (%s); retrying without them.", exc)
        with self._client.messages.stream(**base_kwargs) as stream:
            return stream.get_final_message()

    # ------------------------------------------------------------------
    # extract: structured output, no tools
    # ------------------------------------------------------------------
    def extract(self, *, system: str, prompt: str, schema: Type[T]) -> T:
        response = self._client.messages.parse(
            model=self.settings.model,
            max_tokens=self.settings.extract_max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_format=schema,
        )
        self._guard_refusal(response)
        parsed = getattr(response, "parsed_output", None)
        if parsed is None:  # pragma: no cover - defensive
            raise RuntimeError(f"Structured extraction returned no parsed output for {schema.__name__}.")
        return parsed

    # ------------------------------------------------------------------
    # response parsing
    # ------------------------------------------------------------------
    def _guard_refusal(self, message: Any) -> None:
        if getattr(message, "stop_reason", None) == "refusal":
            details = getattr(message, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            raise RuntimeError(f"Model declined the request (category={category!r}).")

    def _absorb(self, message: Any, transcript: ResearchTranscript) -> None:
        for block in getattr(message, "content", []) or []:
            block_type = getattr(block, "type", "")
            if block_type == "text":
                transcript.text += getattr(block, "text", "")
                for citation in getattr(block, "citations", None) or []:
                    url = getattr(citation, "url", "") or ""
                    if not url:
                        continue
                    transcript.citations.append(
                        Citation(
                            url=url,
                            title=getattr(citation, "title", "") or "",
                            cited_text=(getattr(citation, "cited_text", "") or "").strip(),
                        )
                    )
            elif block_type == "server_tool_use":
                query = (getattr(block, "input", {}) or {}).get("query")
                if query:
                    transcript.queries.append(query)
            elif block_type == "web_search_tool_result":
                content = getattr(block, "content", None)
                # An error result is a single object, a success is a list.
                if not isinstance(content, list):
                    code = getattr(content, "error_code", None)
                    if code:
                        logger.warning("web_search returned error_code=%s", code)
                    continue
                query = transcript.queries[-1] if transcript.queries else ""
                for result in content:
                    url = getattr(result, "url", "") or ""
                    if not url:
                        continue
                    transcript.hits.append(
                        SearchHit(
                            url=url,
                            title=getattr(result, "title", "") or "",
                            query=query,
                            snippet=(getattr(result, "page_age", "") or ""),
                        )
                    )
