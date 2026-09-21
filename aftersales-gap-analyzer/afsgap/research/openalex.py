"""OpenAlex scholarly search.

Adds peer-reviewed grounding to the industry benchmark, independent of whatever
the general web search happens to surface. No API key required; ``mailto``
simply puts the caller in OpenAlex's polite pool.
"""

from __future__ import annotations

import logging
from typing import Any

import requests

from ..models import ScholarlyWork

logger = logging.getLogger(__name__)

OPENALEX_WORKS = "https://api.openalex.org/works"


def reconstruct_abstract(inverted_index: dict[str, list[int]] | None) -> str:
    """OpenAlex stores abstracts as {word: [positions]}; rebuild the sentence."""
    if not inverted_index:
        return ""
    positions: list[tuple[int, str]] = []
    for word, indexes in inverted_index.items():
        for index in indexes:
            positions.append((index, word))
    positions.sort(key=lambda item: item[0])
    return " ".join(word for _, word in positions)


def search_works(
    query: str,
    *,
    per_page: int = 10,
    from_year: int = 2015,
    mailto: str = "",
    timeout: int = 30,
    session: Any | None = None,
) -> list[ScholarlyWork]:
    params: dict[str, Any] = {
        "search": query,
        "per-page": per_page,
        "filter": f"from_publication_date:{from_year}-01-01",
        "sort": "relevance_score:desc",
    }
    if mailto:
        params["mailto"] = mailto

    try:
        getter = session.get if session is not None else requests.get
        response = getter(OPENALEX_WORKS, params=params, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        from ..net import is_tls_trust_error

        if is_tls_trust_error(exc):
            logger.warning(
                "OpenAlex is unreachable: the corporate certificate authority is not trusted. "
                "Run `python -m afsgap doctor` for the fix."
            )
        else:
            logger.warning("OpenAlex query %r failed: %s", query, exc)
        return []

    works: list[ScholarlyWork] = []
    for item in payload.get("results", []):
        abstract = reconstruct_abstract(item.get("abstract_inverted_index"))
        location = item.get("primary_location") or {}
        venue = (location.get("source") or {}).get("display_name", "") or ""
        works.append(
            ScholarlyWork(
                title=item.get("display_name") or "",
                year=item.get("publication_year"),
                venue=venue,
                doi=(item.get("doi") or "").replace("https://doi.org/", ""),
                url=(location.get("landing_page_url") or item.get("id") or ""),
                abstract_excerpt=abstract[:900],
            )
        )
    return works
