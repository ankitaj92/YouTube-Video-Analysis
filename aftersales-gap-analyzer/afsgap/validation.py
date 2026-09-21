"""Final gate over the rendered document.

The last line of defence: whatever the filters and prompts did upstream, the
document that leaves this tool is scanned one more time for excluded topics and
for ungrounded transaction codes.
"""

from __future__ import annotations

import re

from .filters.kpi import KpiFilter
from .filters.tolerance import ToleranceFilter
from .models import RunResult, ValidationReport
from .research.tcode import find_tcode_mentions

# Section numbers differ between the gap and greenfield documents, so match on
# the heading text rather than its number.
_INDUSTRY_SECTION = re.compile(
    r"^## \d+\. Industry practice benchmark(.*?)(?=^## \d+\.)", re.MULTILINE | re.DOTALL
)
_VALIDATION_LOG = re.compile(r"^## \d+\. Exclusion and validation log", re.MULTILINE)
# Text the document writes *about* its own exclusion rules. It necessarily
# names the excluded topics, so it is stripped before the gate runs.
_META_BLOCK = re.compile(r"<!-- afsgap:meta -->.*?<!-- /afsgap:meta -->", re.DOTALL)


def validate_document(
    document: str,
    result: RunResult,
    tolerance: ToleranceFilter,
    kpi: KpiFilter,
    report: ValidationReport,
) -> None:
    body = document_body(document)

    # 1. tolerance topics must be absent from the whole document
    tolerance.assert_clean(body, "final_document", report)

    # 2. the industry section must remain KPI-free
    match = _INDUSTRY_SECTION.search(body)
    if match:
        hits = kpi.matches(match.group(1))
        if hits:
            report.error(
                f"[kpi] the industry benchmark section contains measurement language "
                f"{sorted(set(h.lower() for h in hits))[:8]}."
            )

    # 3. no transaction code may appear that verification did not clear
    verified = {code.tcode.upper() for code in result.sap_research.verified_tcodes}
    rejected = {code.tcode.upper() for code in result.sap_research.dropped_tcodes}
    for candidate in sorted(find_tcode_mentions(body, also_search_for=rejected)):
        if candidate in verified:
            continue
        if candidate in rejected:
            report.error(
                f"[tcode] rejected transaction code '{candidate}' leaked into the document body."
            )
        else:
            report.warn(
                f"[tcode] '{candidate}' is presented as a transaction code but was never verified "
                "against a public SAP source."
            )


def document_body(document: str) -> str:
    """The part of a document that makes claims.

    Excludes three things that legitimately quote what was excluded or
    rejected: the meta blocks describing the exclusion rules, the exclusion and
    validation log, and the rejected-transaction-code table. Publishing what was
    rejected is the point of those sections; scanning them as if they were
    findings would flag the tool's own transparency.

    Shared with the eval harness so the two cannot drift apart.
    """
    return _strip_evidence_tables(_strip_meta(_strip_validation_log(document)))


def _strip_meta(document: str) -> str:
    return _META_BLOCK.sub(" ", document)


def _strip_validation_log(document: str) -> str:
    """The exclusion log quotes removed text on purpose - don't re-flag it."""
    match = _VALIDATION_LOG.search(document)
    return document[: match.start()] if match else document


def _strip_evidence_tables(document: str) -> str:
    """Drop the rejected-T-code table so its own contents don't trip the check."""
    out: list[str] = []
    skipping = False
    for line in document.splitlines():
        if line.startswith("**Rejected transaction codes**"):
            skipping = True
            continue
        if skipping:
            if line.startswith("|") or not line.strip():
                continue
            skipping = False
        out.append(line)
    return "\n".join(out)
