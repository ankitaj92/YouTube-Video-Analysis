"""Pluggable web search.

The hosted path uses Claude's server-side web search tool and needs nothing
here. The local path needs a search engine it can call itself - that is what a
:class:`SearchBackend` provides.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from ..config import Settings


@dataclass
class SearchResult:
    url: str
    title: str = ""
    snippet: str = ""
    query: str = ""


class SearchBackend(Protocol):
    name: str

    def search(
        self,
        query: str,
        max_results: int = 8,
        allowed_domains: Sequence[str] | None = None,
    ) -> list[SearchResult]:
        """Return results for one query, optionally restricted to domains."""


def build_backend(settings: Settings, name: str | None = None) -> SearchBackend:
    backend = (name or settings.search_backend or "duckduckgo").lower()
    if backend in {"duckduckgo", "ddg", "ddgs"}:
        from .duckduckgo import DuckDuckGoBackend

        return DuckDuckGoBackend(settings)
    if backend == "none":
        from .null import NullBackend

        return NullBackend()
    raise ValueError(f"Unknown search backend '{backend}'. Use 'duckduckgo' or 'none'.")
