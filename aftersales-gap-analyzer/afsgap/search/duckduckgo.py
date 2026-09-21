"""Web search via DuckDuckGo and friends.

## Why this is more than a one-line call

The `duckduckgo-search` package became `ddgs`, and `ddgs` is no longer a
DuckDuckGo client - it is a metasearch aggregator. Three of its behaviours ruin
results for this use case unless they are handled:

1. **`backend="auto"` queries Wikipedia and Grokipedia first.** For "SAP EWM
   returns inbound delivery" those return encyclopaedia entries, not SAP
   documentation, and they are queried on every single search.
2. **Its ranker pulls any `wikipedia.org` result to the top**, unconditionally,
   ahead of the SAP page you actually need.
3. **It raises `DDGSException("No results found.")`** instead of returning an
   empty list, so an ordinary empty result reads like a failure.

On top of that, aggregation stops as soon as `max_results` is reached - so with
a small `max_results` the quota fills with junk before a useful engine replies.

This module therefore pins the engines, over-fetches before domain filtering,
and treats "no results" as empty rather than exceptional. It also still works
with the older `duckduckgo_search` package, and falls back to DuckDuckGo's HTML
endpoint when neither library is installed.
"""

from __future__ import annotations

import logging
import re
import time
from html import unescape
from typing import Sequence
from urllib.parse import parse_qs, unquote, urlparse

from ..config import Settings
from ..filters.sources import domain_of
from ..net import build_session, is_tls_trust_error, resolve_verify
from .base import SearchResult

logger = logging.getLogger(__name__)

HTML_ENDPOINT = "https://html.duckduckgo.com/html/"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0 Safari/537.36"
)

# Engines whose results are noise for SAP and industry research. Excluded from
# the default engine list; see the module docstring.
ENCYCLOPAEDIC_ENGINES = {"wikipedia", "grokipedia"}

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


def available_engines() -> set[str]:
    """Engine names the installed ddgs actually offers (empty if unknown)."""
    try:
        from ddgs.engines import ENGINES  # type: ignore

        return set(ENGINES.get("text", {}))
    except Exception:
        return set()


def resolve_engines(requested: str) -> str:
    """Validate the configured engine list against what is installed.

    An unknown name makes ddgs log a warning and silently skip it; if every
    name is unknown the search returns nothing at all. Checking here means a
    stale setting produces one clear message instead of empty research.
    """
    names = [name.strip() for name in (requested or "").split(",") if name.strip()]
    if not names:
        names = ["duckduckgo"]
    installed = available_engines()
    if not installed:
        return ",".join(names)

    usable = [name for name in names if name in installed]
    unknown = [name for name in names if name not in installed]
    if unknown:
        logger.warning(
            "Search engines %s are not available in this version of ddgs (available: %s).",
            ", ".join(unknown), ", ".join(sorted(installed)),
        )
    if not usable:
        usable = sorted(installed - ENCYCLOPAEDIC_ENGINES) or sorted(installed)
        logger.warning("Falling back to engines: %s", ", ".join(usable))
    return ",".join(usable)


class DuckDuckGoBackend:
    name = "duckduckgo"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.timeout = settings.http_timeout
        self.pause = settings.search_pause_seconds
        self.session = build_session(settings)
        # ddgs uses its own HTTP stack, so it needs the CA bundle passed in
        # rather than inheriting the session's settings.
        self.verify = resolve_verify(settings)
        self.engines = resolve_engines(settings.ddgs_backends)

        self._client = None          # ddgs (new) or duckduckgo_search (legacy)
        self._legacy = False
        try:
            from ddgs import DDGS  # type: ignore

            self._client = DDGS
        except ImportError:
            try:
                from duckduckgo_search import DDGS  # type: ignore

                self._client, self._legacy = DDGS, True
                logger.info("Using the legacy duckduckgo_search package.")
            except ImportError:
                logger.info("Neither ddgs nor duckduckgo_search installed - using the HTML endpoint.")

    # ------------------------------------------------------------------
    def search(
        self,
        query: str,
        max_results: int = 8,
        allowed_domains: Sequence[str] | None = None,
    ) -> list[SearchResult]:
        # Domain filtering happens after the engine has answered, so ask for
        # more than we need - otherwise a page of unrelated results leaves
        # nothing once filtered.
        per_query = max_results if not allowed_domains else min(
            max_results * self.settings.search_overfetch, 50
        )

        found: dict[str, SearchResult] = {}
        for index, expanded in enumerate(self._expand(query, allowed_domains)):
            if index and self.pause:
                time.sleep(self.pause)   # engines rate-limit eager clients
            for result in self._one(expanded, per_query):
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
        # Steer the engine at the allowlisted domains that are actually worth
        # searching. Order matters: a login-walled domain such as
        # support.sap.com is barely indexed, so steering at it returns nothing
        # and wastes a query. Priority domains are tried first, and the bare
        # query still runs so nothing on the wider allowlist is missed.
        allowed = list(allowed_domains)
        priority = [domain for domain in self.settings.search_priority_domains if domain in allowed]
        chosen = (priority or allowed)[: self.settings.search_steered_domains]
        return [f"{query} site:{domain}" for domain in chosen] + [query]

    @staticmethod
    def _allowed(url: str, allowed_domains: Sequence[str]) -> bool:
        domain = domain_of(url)
        return any(domain == allowed or domain.endswith("." + allowed) for allowed in allowed_domains)

    # ------------------------------------------------------------------
    def _one(self, query: str, max_results: int) -> list[SearchResult]:
        if self._client is not None:
            try:
                return self._via_package(query, max_results)
            except Exception as exc:
                if self._is_empty_result(exc):
                    logger.debug("No results for %r.", query)
                    return []
                logger.warning("Search library failed for %r (%s); trying the HTML endpoint.", query, exc)
        try:
            return self._via_html(query, max_results)
        except Exception as exc:
            if is_tls_trust_error(exc):
                logger.error(
                    "Search is blocked by corporate TLS interception - the proxy's certificate "
                    "authority is not trusted yet. Run `python -m afsgap doctor` for the fix."
                )
            else:
                logger.warning("DuckDuckGo HTML search failed for %r: %s", query, exc)
            return []

    @staticmethod
    def _is_empty_result(exc: BaseException) -> bool:
        """ddgs raises for an empty result set; that is not a failure."""
        return "no results" in str(exc).lower()

    def _via_package(self, query: str, max_results: int) -> list[SearchResult]:
        kwargs = {"max_results": max_results, "safesearch": "moderate"}
        if not self._legacy:
            # Pin the engines: 'auto' leads with encyclopaedia sources and its
            # ranker promotes wikipedia.org above everything else.
            kwargs["backend"] = self.engines

        results: list[SearchResult] = []
        client = self._client(timeout=self.timeout, verify=self.verify) if not self._legacy else self._client()
        with client as session:
            for item in session.text(query, **kwargs) or []:
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
        response = self.session.post(
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
