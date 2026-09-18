"""Offline stand-in for :class:`ConfluenceClient`.

Serves fixture pages so the Confluence path - search, match scoring, page
selection, the greenfield fallback - can be exercised with no Confluence
instance and no network.
"""

from __future__ import annotations

import json
from pathlib import Path

from .confluence import ConfluencePage, title_similarity


class OfflineConfluenceClient:
    """Reads ``confluence_pages.json``: a list of page objects."""

    def __init__(self, fixtures_dir: Path) -> None:
        self.fixtures_dir = Path(fixtures_dir)
        self._pages = self._load()

    def _load(self) -> list[ConfluencePage]:
        path = self.fixtures_dir / "confluence_pages.json"
        if not path.exists():
            return []
        return [ConfluencePage(**item) for item in json.loads(path.read_text(encoding="utf-8"))]

    def search(self, process_name: str, spaces: list[str] | None = None, limit: int = 10) -> list[ConfluencePage]:
        results: list[ConfluencePage] = []
        for page in self._pages:
            if spaces and page.space_key not in spaces:
                continue
            candidate = ConfluencePage(**page.__dict__)
            candidate.score = title_similarity(process_name, page.title)
            if candidate.score > 0:
                results.append(candidate)
        return sorted(results, key=lambda p: p.score, reverse=True)[:limit]

    def get_page(self, page_id: str) -> ConfluencePage:
        for page in self._pages:
            if page.page_id == page_id:
                return ConfluencePage(**page.__dict__)
        raise LookupError(f"No fixture Confluence page with id {page_id}")
