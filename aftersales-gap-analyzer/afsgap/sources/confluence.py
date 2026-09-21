"""Confluence reader.

Targets the v1 content API (``/rest/api/content/...``), which exists on both
Confluence Cloud (under ``/wiki``) and Data Center / Server - so the same code
works against either estate. Authentication is Basic (Cloud: email + API token)
or Bearer (Data Center personal access token).

Read-only by design: this tool never writes to Confluence.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from ..config import Settings
from ..net import build_session, is_tls_trust_error
from .storage_format import storage_to_text

logger = logging.getLogger(__name__)

STOPWORDS = {"the", "a", "an", "of", "for", "and", "to", "in", "process", "sap"}


class ConfluenceUnavailableError(RuntimeError):
    """Raised when Confluence is not configured or cannot be reached."""


@dataclass
class ConfluencePage:
    page_id: str
    title: str
    space_key: str = ""
    url: str = ""
    version: int = 0
    last_modified: str = ""
    last_modified_by: str = ""
    text: str = ""
    score: float = 0.0
    excerpt: str = ""

    def citation(self) -> str:
        parts = [f"{self.title} (Confluence"]
        if self.space_key:
            parts.append(f", space {self.space_key}")
        if self.version:
            parts.append(f", v{self.version}")
        if self.last_modified:
            parts.append(f", modified {self.last_modified[:10]}")
        return "".join(parts) + ")"


def normalise_base_url(raw: str) -> str:
    """Fix the base URL mistake everyone makes with Confluence Cloud.

    A Cloud site serves Confluence under ``/wiki``: the REST path is
    ``https://site.atlassian.net/wiki/rest/api/...``. Given the site URL alone,
    every call 404s - which looks like "the API is gone" rather than "the path
    is short". Data Center serves it at the root, so only Cloud is adjusted.
    """
    base = (raw or "").strip().rstrip("/")
    if not base:
        return ""
    host = urlparse(base if "//" in base else f"https://{base}").netloc.lower()
    if host.endswith(".atlassian.net") and not base.rstrip("/").endswith("/wiki"):
        return base + "/wiki"
    return base


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOPWORDS and len(t) > 2}


def title_similarity(process_name: str, title: str) -> float:
    """Token overlap between the requested process name and a page title.

    Deliberately simple and explainable: a human reviewing why a page was or was
    not matched can reproduce this in their head.
    """
    wanted, found = _tokens(process_name), _tokens(title)
    if not wanted:
        return 0.0
    overlap = len(wanted & found)
    precision = overlap / len(found) if found else 0.0
    recall = overlap / len(wanted)
    if not overlap:
        return 0.0
    return round((2 * precision * recall) / (precision + recall), 3)


class ConfluenceClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = normalise_base_url(settings.confluence_base_url or "")
        if not self.base_url:
            raise ConfluenceUnavailableError(
                "AFSGAP_CONFLUENCE_BASE_URL is not set. Set it in .env (for example "
                "https://yourcompany.atlassian.net/wiki for Cloud, or "
                "https://confluence.yourcompany.com for Data Center), or pass a process "
                "YAML file instead of a process name."
            )
        self.session = build_session(settings)      # carries the corporate CA settings
        self.session.headers.update({"Accept": "application/json"})
        self._authenticate()

    # ------------------------------------------------------------------
    def _authenticate(self) -> None:
        token = self.settings.confluence_api_token
        email = self.settings.confluence_email
        if not token:
            raise ConfluenceUnavailableError(
                "AFSGAP_CONFLUENCE_API_TOKEN is not set. Use a Cloud API token together with "
                "AFSGAP_CONFLUENCE_EMAIL, or a Data Center personal access token with "
                "AFSGAP_CONFLUENCE_AUTH=bearer."
            )
        mode = (self.settings.confluence_auth or "auto").lower()
        if mode == "auto":
            mode = "basic" if email else "bearer"
        if mode == "basic":
            if not email:
                raise ConfluenceUnavailableError(
                    "Basic authentication needs AFSGAP_CONFLUENCE_EMAIL alongside the API token."
                )
            self.session.auth = (email, token)
        else:
            self.session.headers["Authorization"] = f"Bearer {token}"

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            response = self.session.get(url, params=params, timeout=self.settings.http_timeout)
        except requests.exceptions.SSLError as exc:
            if is_tls_trust_error(exc):
                raise ConfluenceUnavailableError(
                    f"TLS verification failed for {url}. Your Confluence certificate is signed by "
                    "a certificate authority Python does not trust yet - run "
                    "`python -m afsgap doctor` for the fix."
                ) from exc
            raise
        if response.status_code in {401, 403}:
            raise ConfluenceUnavailableError(
                f"Confluence rejected the credentials ({response.status_code}) for {url}. "
                "Check the token, the auth mode, and that the account can read the space."
            )
        if response.status_code == 404:
            raise ConfluenceUnavailableError(
                f"Confluence returned 404 for {url}.\n"
                "The usual cause is the base URL. Confluence Cloud serves the API under /wiki:\n"
                "  AFSGAP_CONFLUENCE_BASE_URL=https://yoursite.atlassian.net/wiki\n"
                "Data Center serves it at the root (no /wiki). Check the value in .env, then "
                "run `python -m afsgap doctor`."
            )
        response.raise_for_status()
        return response.json()

    # ------------------------------------------------------------------
    def search(self, process_name: str, spaces: list[str] | None = None, limit: int = 10) -> list[ConfluencePage]:
        """Find candidate pages for a process name, best match first.

        Two passes: an exact-ish title match, then a full-text match. Title hits
        rank above body hits because a process page is normally titled after the
        process.
        """
        spaces = spaces if spaces is not None else self.settings.confluence_spaces
        escaped = process_name.replace('"', '\\"')
        space_clause = ""
        if spaces:
            space_list = ", ".join(f'"{space}"' for space in spaces)
            space_clause = f" AND space in ({space_list})"

        queries = [
            f'type = page AND title ~ "{escaped}"{space_clause}',
            f'type = page AND text ~ "{escaped}"{space_clause}',
        ]

        found: dict[str, ConfluencePage] = {}
        for cql in queries:
            try:
                payload = self._get(
                    "/rest/api/content/search",
                    {"cql": cql, "limit": limit, "expand": "version,space"},
                )
            except requests.HTTPError as exc:
                logger.warning("Confluence search failed for %s: %s", cql, exc)
                continue
            for item in payload.get("results", []):
                page = self._to_page(item)
                if page.page_id in found:
                    continue
                page.score = title_similarity(process_name, page.title)
                found[page.page_id] = page

        pages = sorted(found.values(), key=lambda p: p.score, reverse=True)
        return pages[:limit]

    def get_page(self, page_id: str) -> ConfluencePage:
        cached = self._cache_path(page_id)
        if cached.exists():
            return ConfluencePage(**json.loads(cached.read_text(encoding="utf-8")))

        payload = self._get(
            f"/rest/api/content/{page_id}",
            {"expand": "body.storage,version,space"},
        )
        page = self._to_page(payload)
        storage = (((payload.get("body") or {}).get("storage") or {}).get("value")) or ""
        page.text = storage_to_text(storage)

        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(page.__dict__, ensure_ascii=False, indent=2), encoding="utf-8")
        return page

    # ------------------------------------------------------------------
    def _cache_path(self, page_id: str) -> Path:
        return self.settings.cache_dir / "confluence" / f"{page_id}.json"

    def _to_page(self, item: dict[str, Any]) -> ConfluencePage:
        version = item.get("version") or {}
        links = item.get("_links") or {}
        webui = links.get("webui") or ""
        return ConfluencePage(
            page_id=str(item.get("id", "")),
            title=item.get("title", ""),
            space_key=(item.get("space") or {}).get("key", ""),
            url=f"{self.base_url}{webui}" if webui.startswith("/") else (webui or self.base_url),
            version=int(version.get("number") or 0),
            last_modified=version.get("when", "") or "",
            last_modified_by=((version.get("by") or {}).get("displayName", "") or ""),
            excerpt=((item.get("excerpt") or "")[:400]),
        )
