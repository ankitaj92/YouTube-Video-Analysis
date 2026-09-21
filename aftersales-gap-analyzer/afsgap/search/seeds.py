"""Seed-URL "search": no search engine involved.

For networks where the web filter blocks search engines but leaves the sites
themselves reachable - common in an SAP shop, where ``help.sap.com`` is
allowlisted and ``duckduckgo.com`` is not. You paste in the URLs, this backend
matches them to each query by topic, and the normal pipeline takes over:
pages are fetched, filtered, extracted from, and T-codes are still verified
against the literal page text.

Seeds are *your* starting points, not search results, and the design document
says so: they appear in the evidence register like any other source, and a URL
that cannot be fetched simply produces nothing.

Edit ``data/seed_sources.yaml``.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Sequence

import yaml

from ..config import Settings
from ..filters.sources import domain_of
from .base import SearchResult

logger = logging.getLogger(__name__)

_WORD = re.compile(r"[a-z0-9]+")
STOPWORDS = {"the", "a", "an", "of", "for", "and", "to", "in", "sap", "site", "process"}


def _tokens(text: str) -> set[str]:
    return {t for t in _WORD.findall((text or "").lower()) if t not in STOPWORDS and len(t) > 2}


class SeedsBackend:
    name = "seeds"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.entries = self._load(settings.seed_sources_path)
        if not self.entries:
            logger.warning(
                "The seeds backend is selected but %s lists no sources. Add the URLs you want "
                "researched - see the comments in that file.", settings.seed_sources_path,
            )

    @staticmethod
    def _load(path: str | Path) -> list[dict]:
        file = Path(path)
        if not file.exists():
            return []
        data = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
        entries = []
        for item in data.get("sources", []) or []:
            if isinstance(item, str):
                entries.append({"url": item, "title": "", "topics": []})
            elif isinstance(item, dict) and item.get("url"):
                entries.append(
                    {
                        "url": item["url"],
                        "title": item.get("title", ""),
                        "topics": [str(t) for t in (item.get("topics") or [])],
                    }
                )
        return entries

    def search(
        self,
        query: str,
        max_results: int = 8,
        allowed_domains: Sequence[str] | None = None,
    ) -> list[SearchResult]:
        wanted = _tokens(query)
        scored: list[tuple[float, SearchResult]] = []

        for entry in self.entries:
            url = entry["url"]
            if allowed_domains:
                domain = domain_of(url)
                if not any(domain == d or domain.endswith("." + d) for d in allowed_domains):
                    continue
            # Whether a seed is topic-restricted depends on its `topics` list
            # alone. The title only contributes to ranking - a seed with no
            # topics is a general-purpose starting point: always eligible, but
            # ranked below one that matches the query.
            declared = _tokens(" ".join(entry["topics"]))
            score = len(wanted & (declared | _tokens(entry["title"]))) if declared else 0.1
            if declared and not score:
                continue
            scored.append(
                (score, SearchResult(url=url, title=entry["title"], query=query, snippet="seed source"))
            )

        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [result for _, result in scored[:max_results]]
