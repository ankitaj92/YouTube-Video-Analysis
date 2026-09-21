"""Mojeek search backend.

An independent index with its own crawler, no API key, and a simple HTML result
page. Worth having because corporate web filters commonly block
``duckduckgo.com`` by category while leaving smaller engines reachable - when
DuckDuckGo returns nothing on a managed network, it is usually policy rather
than rate limiting.
"""

from __future__ import annotations

import logging
import re
from html import unescape
from typing import Sequence

from ..config import Settings
from ..filters.sources import domain_of
from ..net import build_session, is_tls_trust_error
from .base import SearchResult

logger = logging.getLogger(__name__)

ENDPOINT = "https://www.mojeek.com/search"
_RESULT = re.compile(
    r'<a[^>]+href="(?P<href>https?://[^"]+)"[^>]*class="[^"]*ob[^"]*"[^>]*>(?P<title>.*?)</a>'
    r'|<h2><a[^>]+href="(?P<href2>https?://[^"]+)"[^>]*>(?P<title2>.*?)</a></h2>',
    re.DOTALL | re.IGNORECASE,
)
_TAGS = re.compile(r"<[^>]+>")


def _clean(text: str) -> str:
    return " ".join(unescape(_TAGS.sub(" ", text or "")).split())


class MojeekBackend:
    name = "mojeek"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.session = build_session(settings)

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
                ENDPOINT, params={"q": terms}, timeout=self.settings.http_timeout
            )
            response.raise_for_status()
        except Exception as exc:
            if is_tls_trust_error(exc):
                logger.error("Mojeek blocked by corporate TLS interception - run `afsgap doctor`.")
            else:
                logger.warning("Mojeek search failed for %r: %s", terms, exc)
            return []

        results: list[SearchResult] = []
        seen: set[str] = set()
        for match in _RESULT.finditer(response.text):
            url = match.group("href") or match.group("href2") or ""
            title = match.group("title") or match.group("title2") or ""
            if not url or url in seen or "mojeek.com" in url:
                continue
            if allowed_domains:
                domain = domain_of(url)
                if not any(domain == d or domain.endswith("." + d) for d in allowed_domains):
                    continue
            seen.add(url)
            results.append(SearchResult(url=url, title=_clean(title), query=terms))
            if len(results) >= max_results:
                break
        return results
