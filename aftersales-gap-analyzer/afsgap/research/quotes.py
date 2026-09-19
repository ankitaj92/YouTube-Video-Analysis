"""Evidence quote verification.

A quote is only evidence if the page it cites actually contains it. The hosted
path gets verbatim ``cited_text`` from the search tool, so this mostly idles
there; on the local path, where a smaller model writes the quotes itself, it is
the difference between a citation and a paraphrase presented as one.

Matching is normalised (whitespace, quote characters, case) and then falls back
to token coverage, because a model that drops a comma has not fabricated a
source. A quote that is neither present nor close to present is dropped, and the
drop is recorded - the claim survives, its false citation does not.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from ..models import ExclusionRecord, ValidationReport

logger = logging.getLogger(__name__)

_WS = re.compile(r"\s+")
_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-"})
_WORD = re.compile(r"[a-z0-9]+")

MIN_QUOTE_WORDS = 4
TOKEN_COVERAGE = 0.85


def normalise(text: str) -> str:
    return _WS.sub(" ", (text or "").translate(_QUOTES)).strip().lower()


def quote_supported(quote: str, page_text: str) -> bool:
    """True when ``page_text`` contains the quote, exactly or near enough."""
    if not quote or not page_text:
        return False
    needle, haystack = normalise(quote), normalise(page_text)
    if len(_WORD.findall(needle)) < MIN_QUOTE_WORDS:
        return False          # too short to be meaningful evidence
    if needle in haystack:
        return True
    # Tolerate small transcription drift, not invention: most of the quote's
    # words must appear, and its longest phrases must be present.
    words = _WORD.findall(needle)
    page_words = set(_WORD.findall(haystack))
    coverage = sum(1 for word in words if word in page_words) / len(words)
    if coverage < TOKEN_COVERAGE:
        return False
    fragments = [" ".join(words[i:i + 5]) for i in range(0, max(len(words) - 4, 1), 5)]
    return any(fragment in haystack for fragment in fragments)


def verify_evidence(
    payload: Any,
    pages: dict[str, str],
    stage: str,
    report: ValidationReport,
) -> Any:
    """Walk a dumped model payload and drop unsupported ``evidence`` entries."""
    if not pages:
        return payload   # nothing retrieved to check against (e.g. hosted path)

    dropped = 0

    def walk(node: Any) -> Any:
        nonlocal dropped
        if isinstance(node, list):
            return [walk(item) for item in node]
        if not isinstance(node, dict):
            return node

        result = {key: walk(value) for key, value in node.items()}
        evidence = result.get("evidence")
        if isinstance(evidence, list):
            kept = []
            for item in evidence:
                if not isinstance(item, dict):
                    continue
                url, quote = item.get("url", ""), item.get("quote", "")
                page = pages.get(url)
                if page is None:
                    # The cited page was never retrieved in this run.
                    report.exclusions.append(
                        ExclusionRecord(
                            stage=stage, location=url or "(no url)", excluded_text=quote[:200],
                            matched_terms=["citation points at a page this run did not retrieve"],
                            rule="unverified_quote",
                        )
                    )
                    dropped += 1
                    continue
                if not quote_supported(quote, page):
                    report.exclusions.append(
                        ExclusionRecord(
                            stage=stage, location=url, excluded_text=quote[:200],
                            matched_terms=["quote not found in the cited page"],
                            rule="unverified_quote",
                        )
                    )
                    dropped += 1
                    continue
                kept.append(item)
            result["evidence"] = kept
        return result

    cleaned = walk(payload)
    if dropped:
        report.warn(
            f"[quote] {dropped} evidence quote(s) at {stage} were not found in the page they cite "
            "and were removed. The claims remain; their citations do not."
        )
    return cleaned
