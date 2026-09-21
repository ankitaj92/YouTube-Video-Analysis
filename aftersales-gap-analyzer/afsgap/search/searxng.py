"""SearXNG search backend.

SearXNG is a self-hosted metasearch engine. If your organisation runs one - or
you run one locally in Docker - it is the most reliable option on a restricted
network: it is inside the perimeter, it needs no API key, and it returns JSON.

Set ``AFSGAP_SEARXNG_URL`` to the instance, for example
``http://localhost:8080`` or ``https://searx.yourcompany.com``.
"""

from __future__ import annotations

import logging
from typing import Sequence

from ..config import Settings
from ..filters.sources import domain_of
from ..net import build_session, is_tls_trust_error
from .base import SearchResult

logger = logging.getLogger(__name__)


class SearxngBackend:
    name = "searxng"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = (settings.searxng_url or "").rstrip("/")
        self.session = build_session(settings)
        if not self.base_url:
            raise ValueError(
                "AFSGAP_SEARXNG_URL is not set. Point it at a SearXNG instance, or choose a "
                "different search backend."
            )

    def search(
        self,
        query: str,
        max_results: int = 8,
        allowed_domains: Sequence[str] | None = None,
    ) -> list[SearchResult]:
        terms = query
        if allowed_domains:
            priority = [d for d in self.settings.search_priority_domains if d in list(allowed_domains)]
            if priority:
                terms = f"{query} site:{priority[0]}"
        try:
            response = self.session.get(
                f"{self.base_url}/search",
                params={"q": terms, "format": "json", "language": "en"},
                timeout=self.settings.http_timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            if is_tls_trust_error(exc):
                logger.error("SearXNG blocked by corporate TLS interception - run `afsgap doctor`.")
            else:
                logger.warning("SearXNG search failed for %r: %s", terms, exc)
            return []

        results: list[SearchResult] = []
        for item in payload.get("results", []):
            url = item.get("url") or ""
            if not url:
                continue
            if allowed_domains:
                domain = domain_of(url)
                if not any(domain == d or domain.endswith("." + d) for d in allowed_domains):
                    continue
            results.append(
                SearchResult(
                    url=url,
                    title=item.get("title", ""),
                    snippet=(item.get("content") or "")[:300],
                    query=terms,
                )
            )
            if len(results) >= max_results:
                break
        return results
