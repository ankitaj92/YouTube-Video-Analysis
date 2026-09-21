"""Pluggable web search.

The hosted path uses Claude's server-side web search tool and needs nothing
here. The local path needs a search engine it can call itself - that is what a
:class:`SearchBackend` provides.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol, Sequence

from ..config import Settings

logger = logging.getLogger(__name__)


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


KNOWN_BACKENDS = ("duckduckgo", "mojeek", "searxng", "seeds", "none")


def _build_one(settings: Settings, backend: str) -> SearchBackend:
    if backend in {"duckduckgo", "ddg", "ddgs"}:
        from .duckduckgo import DuckDuckGoBackend

        return DuckDuckGoBackend(settings)
    if backend == "mojeek":
        from .mojeek import MojeekBackend

        return MojeekBackend(settings)
    if backend == "searxng":
        from .searxng import SearxngBackend

        return SearxngBackend(settings)
    if backend == "seeds":
        from .seeds import SeedsBackend

        return SeedsBackend(settings)
    if backend == "none":
        from .null import NullBackend

        return NullBackend()
    raise ValueError(
        f"Unknown search backend '{backend}'. Available: {', '.join(KNOWN_BACKENDS)}. "
        "Several can be chained with commas, e.g. duckduckgo,mojeek,seeds"
    )


def build_backend(settings: Settings, name: str | None = None) -> SearchBackend:
    """Build one backend, or a fallback chain from a comma-separated list."""
    requested = (name or settings.search_backend or "duckduckgo").lower()
    names = [part.strip() for part in requested.split(",") if part.strip()]
    if len(names) <= 1:
        return _build_one(settings, names[0] if names else "duckduckgo")

    from .chain import ChainBackend

    backends = []
    for backend_name in names:
        try:
            backends.append(_build_one(settings, backend_name))
        except Exception as exc:
            logger.warning("Search backend '%s' is unavailable and was skipped: %s", backend_name, exc)
    if not backends:
        raise ValueError(f"None of the requested search backends could be built: {requested}")
    return ChainBackend(backends)
