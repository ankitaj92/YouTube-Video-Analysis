"""A search backend that finds nothing.

Useful for a dry run of the local pipeline with no outbound traffic at all: the
research stages then return no sources, and the document says so honestly
instead of inventing content.
"""

from __future__ import annotations

from typing import Sequence

from .base import SearchResult


class NullBackend:
    name = "none"

    def search(
        self,
        query: str,
        max_results: int = 8,
        allowed_domains: Sequence[str] | None = None,
    ) -> list[SearchResult]:
        return []
