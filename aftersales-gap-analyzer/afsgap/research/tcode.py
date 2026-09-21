"""T-code grounding.

Rule: a transaction code appears in a design document only when a public SAP
source explicitly supports it. "The model said so" is not support. A candidate
survives three checks:

1. **Shape**  - it looks like an SAP transaction code (incl. ``/NAMESPACE/TCODE``).
2. **Domain** - the cited URL is on the official SAP allowlist.
3. **Literal** - the fetched page text contains the code as a standalone token.

Anything that fails is dropped and recorded in ``dropped_tcodes`` with the
reason, so the design document can state what was rejected and why.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from ..filters.sources import SourceClassifier
from ..models import DroppedTCode, TCodeCandidate, VerifiedTCode
from .http import fetch_text

logger = logging.getLogger(__name__)

# VA01, VL01N, MIGO, MB1B, LT01, /SCWM/PRDI, /DBM/ORDER
TCODE_SHAPE = re.compile(r"^(?:/[A-Z0-9]{2,10}/)?[A-Z][A-Z0-9_]{1,19}$")

# Words that pass the shape test but are obviously not transaction codes.
NOT_TCODES = {
    "SAP", "ERP", "ECC", "EWM", "SD", "MM", "PP", "FI", "CO", "CS", "PM", "QM", "WM",
    "CMH", "SOP", "OEM", "KPI", "API", "GUI", "IDOC", "EDI", "BAPI", "BADI", "RFC",
    "HANA", "FIORI", "S4", "S4HANA", "ATP", "GR", "GI", "PO", "SO", "RMA", "VIN",
}


def looks_like_tcode(value: str) -> bool:
    candidate = value.strip().upper()
    if not TCODE_SHAPE.match(candidate):
        return False
    if candidate in NOT_TCODES:
        return False
    # Require either a digit or a namespace - pure alphabetic codes such as
    # "MIGO" are real, so keep a small allowance for 4-letter codes.
    if candidate.startswith("/"):
        return True
    if any(character.isdigit() for character in candidate):
        return True
    return 3 <= len(candidate) <= 6


def literal_match(page_text: str, tcode: str) -> bool:
    if not page_text:
        return False
    pattern = re.compile(rf"(?<![A-Z0-9_/]){re.escape(tcode)}(?![A-Z0-9_])", re.IGNORECASE)
    return bool(pattern.search(page_text))


class TCodeVerifier:
    def __init__(
        self,
        classifier: SourceClassifier,
        cache_dir: Path,
        timeout: int = 30,
        require_literal_evidence: bool = True,
        session=None,
    ) -> None:
        self.classifier = classifier
        self.cache_dir = cache_dir
        self.timeout = timeout
        self.require_literal_evidence = require_literal_evidence
        self.session = session

    def verify(self, candidates: list[TCodeCandidate]) -> tuple[list[VerifiedTCode], list[DroppedTCode]]:
        verified: list[VerifiedTCode] = []
        dropped: list[DroppedTCode] = []
        seen: set[str] = set()

        for candidate in candidates:
            code = (candidate.tcode or "").strip().upper()
            if not code:
                continue
            if code in seen:
                continue
            seen.add(code)

            if not looks_like_tcode(code):
                dropped.append(DroppedTCode(tcode=code, source_url=candidate.source_url,
                                            reason="does not match SAP transaction code shape"))
                continue
            if not candidate.source_url:
                dropped.append(DroppedTCode(tcode=code, reason="no source URL supplied by research stage"))
                continue
            if not self.classifier.is_sap_official(candidate.source_url):
                dropped.append(DroppedTCode(tcode=code, source_url=candidate.source_url,
                                            reason="source is not an official public SAP domain"))
                continue

            if self.require_literal_evidence:
                page = fetch_text(candidate.source_url, self.cache_dir, self.timeout, self.session)
                if not page:
                    dropped.append(DroppedTCode(tcode=code, source_url=candidate.source_url,
                                                reason="SAP page could not be retrieved for literal verification"))
                    continue
                if not literal_match(page, code):
                    dropped.append(DroppedTCode(tcode=code, source_url=candidate.source_url,
                                                reason="transaction code not literally present on the cited SAP page"))
                    continue

            verified.append(
                VerifiedTCode(
                    tcode=code,
                    purpose=candidate.purpose,
                    sap_area=candidate.sap_area,
                    source_url=candidate.source_url,
                    supporting_quote=candidate.supporting_quote,
                )
            )
        return verified, dropped


# Cue-based detection for the *output* side of the guard. Scanning prose for
# anything that merely looks like a code flags acronyms (ABAP, DOI, URL) and
# section ids (G01); requiring an explicit cue makes the check precise, and
# rejected codes are searched for directly regardless of cue.
_URL = re.compile(r"https?://\S+")
_CUE_BEFORE = re.compile(
    r"\b(?:transaction|transactions|t-?code|t-?codes|tcode)\s+(?:code\s+)?((?:/[A-Z0-9]{2,10}/)?[A-Z][A-Z0-9_]{1,19})\b"
)
_CUE_AFTER = re.compile(
    r"\b((?:/[A-Z0-9]{2,10}/)?[A-Z][A-Z0-9_]{1,19})\s+(?:transaction|t-?code|tcode)\b"
)


def find_tcode_mentions(text: str, also_search_for: set[str] | None = None) -> set[str]:
    """Return transaction codes a piece of prose actually claims.

    A token counts when it is introduced by a transaction-code cue, or when it
    is one of ``also_search_for`` (used to catch a rejected code leaking into
    the document body). URLs are removed first so path segments never match.
    """
    body = _URL.sub(" ", text or "")
    found: set[str] = set()
    for pattern in (_CUE_BEFORE, _CUE_AFTER):
        for match in pattern.finditer(body):
            candidate = match.group(1).upper()
            if looks_like_tcode(candidate):
                found.add(candidate)
    for candidate in also_search_for or set():
        if literal_match(body, candidate):
            found.add(candidate.upper())
    return found
