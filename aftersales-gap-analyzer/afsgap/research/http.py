"""Cached HTTP fetching used for T-code verification.

Verification must read the actual page, not the model's recollection of it, so
every fetch is cached on disk by URL hash - a re-run of the same process costs
no extra network calls.
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

_TAG = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_MARKUP = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\r\f\v]+")

USER_AGENT = "afsgap-process-research/0.1 (+internal SAP S/4HANA design study)"


def _cache_path(cache_dir: Path, url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
    return cache_dir / "pages" / f"{digest}.txt"


def fetch_text(url: str, cache_dir: Path, timeout: int = 30, session: Any | None = None) -> str:
    """Return the page as plain text ("" when it cannot be retrieved).

    ``session`` carries the corporate TLS settings; without one, a plain request
    is made, which is fine on an unmanaged network.
    """
    path = _cache_path(cache_dir, url)
    if path.exists():
        return path.read_text(encoding="utf-8", errors="ignore")

    try:
        if session is not None:
            response = session.get(url, timeout=timeout)
        else:
            response = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
        response.raise_for_status()
        text = html_to_text(response.text)
    except Exception as exc:  # network failure must not break the run
        from ..net import is_tls_trust_error

        if is_tls_trust_error(exc):
            logger.warning(
                "Could not fetch %s: the corporate certificate authority is not trusted. "
                "Run `python -m afsgap doctor` for the fix.", url,
            )
        else:
            logger.warning("Could not fetch %s for verification: %s", url, exc)
        text = ""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return text


def html_to_text(html: str) -> str:
    text = _TAG.sub(" ", html)
    text = _MARKUP.sub(" ", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = _WS.sub(" ", text)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())
