"""DuckDuckGo search backend.

No API key and no account - which is the point for a local test setup. Two
transports, tried in order:

1. the ``ddgs`` package, if installed (maintained, handles the moving parts);
2. DuckDuckGo's HTML endpoint, parsed directly, as a fallback.

Domain restriction is done twice: ``site:`` operators to steer the engine, and a
hard filter on the returned domains, because ``site:`` is a hint rather than a
guarantee. That matters for SAP research, where a result from outside the
official SAP domains must never become an "SAP fact".
"""

from __future__ import annotations

import logging
import re
import time
from html import unescape
from typing import Sequence
from urllib.parse import parse_qs, unquote, urlparse

import requests

from ..config import Settings
from ..filters.sources import domain_of
from .base import SearchResult

logger = logging.getLogger(__name__)

HTML_ENDPOINT = "https://html.duckduckgo.com/html/"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0 Safari/537.36"
)

_RESULT = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>'
    r'(?:.*?<a[^>]+class="result__snippet"[^>]*>(?P<snippet>.*?)</a>)?',
    re.DOTALL | re.IGNORECASE,
)
_TAGS = re.compile(r"<[^>]+>")


def _clean(text: str) -> str:
    return " ".join(unescape(_TAGS.sub(" ", text or "")).split())


def _unwrap(href: str) -> str:
    """DuckDuckGo wraps results as /l/?uddg=<encoded url> - unwrap those."""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg")
        if target:
            return unquote(target[0])
    return href


class DuckDuckGoBackend:
    name = "duckduckgo"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.timeout = settings.http_timeout
        self.pause = settings.search_pause_seconds
        self._ddgs = None
        try:
            from ddgs import DDGS  # type: ignore

            self._ddgs = DDGS
        except ImportError:
            logger.info("ddgs package not installed - using the DuckDuckGo HTML endpoint.")

    # ------------------------------------------------------------------
    def search(
        self,
        query: str,
        max_results: int = 8,
        allowed_domains: Sequence[str] | None = None,
    ) -> list[SearchResult]:
        queries = self._expand(query, allowed_domains)
        found: dict[str, SearchResult] = {}
        for index, expanded in enumerate(queries):
            if index and self.pause:
                time.sleep(self.pause)   # DuckDuckGo rate-limits eager clients
            for result in self._one(expanded, max_results):
                if not result.url or result.url in found:
                    continue
                if allowed_domains and not self._allowed(result.url, allowed_domains):
                    continue
                found[result.url] = result
                if len(found) >= max_results:
                    return list(found.values())
        return list(found.values())

    def _expand(self, query: str, allowed_domains: Sequence[str] | None) -> list[str]:
        if not allowed_domains:
            return [query]
        # Steer the engine at the two or three most useful domains, then still
        # run the bare query so nothing on the wider allowlist is missed.
        steered = [f"{query} site:{domain}" for domain in list(allowed_domains)[:3]]
        return steered + [query]

    @staticmethod
    def _allowed(url: str, allowed_domains: Sequence[str]) -> bool:
        domain = domain_of(url)
        return any(domain == allowed or domain.endswith("." + allowed) for allowed in allowed_domains)

    # ------------------------------------------------------------------
    def _one(self, query: str, max_results: int) -> list[SearchResult]:
        if self._ddgs is not None:
            try:
                return self._via_package(query, max_results)
            except Exception as exc:  # the package raises its own exception types
                logger.warning("ddgs search failed for %r (%s); falling back to HTML.", query, exc)
        try:
            return self._via_html(query, max_results)
        except Exception as exc:
            logger.warning("DuckDuckGo HTML search failed for %r: %s", query, exc)
            return []

    def _via_package(self, query: str, max_results: int) -> list[SearchResult]:
        results: list[SearchResult] = []
        with self._ddgs() as client:
            for item in client.text(query, max_results=max_results, safesearch="moderate"):
                url = item.get("href") or item.get("url") or item.get("link") or ""
                results.append(
                    SearchResult(
                        url=_unwrap(url),
                        title=_clean(item.get("title", "")),
                        snippet=_clean(item.get("body") or item.get("snippet") or item.get("description") or ""),
                        query=query,
                    )
                )
        return results

    def _via_html(self, query: str, max_results: int) -> list[SearchResult]:
        response = requests.post(
            HTML_ENDPOINT,
            data={"q": query},
            timeout=self.timeout,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
        )
        response.raise_for_status()
        results: list[SearchResult] = []
        for match in _RESULT.finditer(response.text):
            results.append(
                SearchResult(
                    url=_unwrap(match.group("href")),
                    title=_clean(match.group("title")),
                    snippet=_clean(match.group("snippet") or ""),
                    query=query,
                )
            )
            if len(results) >= max_results:
                break
        return results
