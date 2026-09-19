"""A search backend that serves fixture results, for tests and dry runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

from afsgap.filters.sources import domain_of
from afsgap.search.base import SearchResult


class FakeSearchBackend:
    name = "fake"

    def __init__(self, fixtures_dir: Path) -> None:
        pages = json.loads((Path(fixtures_dir) / "pages.json").read_text(encoding="utf-8"))
        self.results = [
            SearchResult(url=url, title=text.split(".")[1].strip() if "." in text else url, snippet=text[:120])
            for url, text in pages.items()
        ]
        self.queries: list[str] = []

    def search(self, query: str, max_results: int = 8, allowed_domains: Sequence[str] | None = None):
        self.queries.append(query)
        results = self.results
        if allowed_domains:
            results = [
                r for r in results
                if any(domain_of(r.url) == d or domain_of(r.url).endswith("." + d) for d in allowed_domains)
            ]
        return results[:max_results]
