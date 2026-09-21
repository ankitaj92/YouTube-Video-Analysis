"""Try several search backends in order until one returns something.

On a restricted network the useful configuration is a chain: the engine you
prefer, a fallback engine that the web filter may not block, and finally your
own seed URLs, which need no search engine at all.

    AFSGAP_SEARCH=duckduckgo,mojeek,seeds

A backend that raises is skipped rather than failing the run, and a backend that
returns nothing simply hands over to the next.
"""

from __future__ import annotations

import logging
from typing import Sequence

from .base import SearchResult

logger = logging.getLogger(__name__)


class ChainBackend:
    def __init__(self, backends: list) -> None:
        self.backends = backends
        self.name = "+".join(backend.name for backend in backends) or "none"

    def search(
        self,
        query: str,
        max_results: int = 8,
        allowed_domains: Sequence[str] | None = None,
    ) -> list[SearchResult]:
        for backend in self.backends:
            try:
                results = backend.search(query, max_results=max_results, allowed_domains=allowed_domains)
            except Exception as exc:
                logger.warning("Search backend %s failed for %r: %s", backend.name, query, exc)
                continue
            if results:
                if backend is not self.backends[0]:
                    logger.info("Search fell back to %s for %r.", backend.name, query)
                return results
        return []
