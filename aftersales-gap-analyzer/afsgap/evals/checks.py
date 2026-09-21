"""Deterministic scoring: no model, no network, no judgement calls.

These are the checks that can be wrong in only one direction. They read the run
record and the rendered document and answer yes/no questions about them, so
they cost nothing and can run on every historical run you still have.

Compliance failures are defects rather than low scores: the point of the
pipeline is that a tolerance statement or an unverified transaction code cannot
reach the document, so a single breach is worth more attention than any amount
of prose quality.
"""

from __future__ import annotations

import re

from ..filters.kpi import KpiFilter
from ..filters.segmentation import segment
from ..filters.sources import SourceClassifier
from ..filters.tolerance import ToleranceFilter
from ..models import RunResult
from ..research.quotes import quote_supported
from ..research.tcode import find_tcode_mentions
from .schema import CheckResult, EvalCase

_INDUSTRY = re.compile(r"^## \d+\. Industry practice benchmark(.*?)(?=^## \d+\.)", re.MULTILINE | re.DOTALL)

REQUIRED_SECTIONS_GAP = ["Verdict", "Process source", "SAP standard reference",
                         "Industry practice benchmark", "Gap analysis", "TO-BE process design",
                         "Evidence register", "Exclusion and validation log"]
REQUIRED_SECTIONS_GREENFIELD = ["Recommendation summary", "Process source", "SAP standard reference",
                                "Industry practice benchmark", "Recommended process design",
                                "Design decisions", "Evidence register",
                                "Exclusion and validation log"]


def body_of(document: str) -> str:
    """The claim-making part of the document.

    Delegates to the production gate's own definition so the eval scores
    exactly what the gate guards - including its exclusion of the
    rejected-transaction-code table, which exists precisely to publish what was
    thrown away.
    """
    from ..validation import document_body

    return document_body(document)


def run_checks(case: EvalCase, result: RunResult, document: str) -> list[CheckResult]:
    checks: list[CheckResult] = []
    body = body_of(document)
    tolerance, kpi, classifier = ToleranceFilter(), KpiFilter(), SourceClassifier()
    tolerance.allow_process_name(result.process_name)

    # ---------------- compliance ----------------
    hits: list[str] = []
    for part in segment(body):
        hits.extend(tolerance.matches(part))
    checks.append(CheckResult(
        id="no_tolerance_content", family="compliance",
        outcome="pass" if not hits else "fail",
        detail="clean" if not hits else f"found {sorted(set(h.lower() for h in hits))[:5]}",
    ))

    industry = _INDUSTRY.search(body)
    kpi_hits = kpi.matches(industry.group(1)) if industry else []
    checks.append(CheckResult(
        id="industry_is_kpi_free", family="compliance",
        outcome="pass" if not kpi_hits else "fail",
        detail="clean" if not kpi_hits else f"found {sorted(set(h.lower() for h in kpi_hits))[:5]}",
    ))

    verified = {code.tcode.upper() for code in result.sap_research.verified_tcodes}
    rejected = {code.tcode.upper() for code in result.sap_research.dropped_tcodes}
    leaked = sorted(find_tcode_mentions(body, also_search_for=rejected) - verified)
    checks.append(CheckResult(
        id="only_verified_tcodes", family="compliance",
        outcome="pass" if not leaked else "fail",
        detail="clean" if not leaked else f"unverified codes cited: {leaked}",
    ))

    forbidden_found = [phrase for phrase in case.forbidden if phrase.lower() in body.lower()]
    checks.append(CheckResult(
        id="no_forbidden_statements", family="compliance",
        outcome="pass" if not forbidden_found else "fail",
        detail="clean" if not forbidden_found else f"said {forbidden_found}",
    ))

    required = REQUIRED_SECTIONS_GREENFIELD if result.mode == "greenfield" else REQUIRED_SECTIONS_GAP
    missing = [name for name in required if name not in document]
    checks.append(CheckResult(
        id="document_complete", family="compliance",
        outcome="pass" if not missing else "fail",
        detail="all sections present" if not missing else f"missing {missing}",
    ))

    checks.append(CheckResult(
        id="validation_passed", family="compliance",
        outcome="pass" if result.validation.passed else "fail",
        detail="; ".join(result.validation.errors[:2]) or "no errors",
    ))

    # ---------------- grounding ----------------
    bad_sap = [s.url for s in result.sap_research.sources if not classifier.is_sap_official(s.url)]
    checks.append(CheckResult(
        id="sap_sources_are_official", family="grounding",
        outcome="pass" if not bad_sap else "fail",
        detail="all official" if not bad_sap else f"off-allowlist: {bad_sap[:3]}",
    ))

    bad_industry = [s.url for s in result.industry_research.sources
                    if not classifier.is_credible_industry(s.url)]
    checks.append(CheckResult(
        id="industry_sources_are_credible", family="grounding",
        outcome="pass" if not bad_industry else "fail",
        detail="all credible" if not bad_industry else f"off-allowlist: {bad_industry[:3]}",
    ))

    checks.append(_tcode_evidence_check(result))
    checks.append(_quote_check(result))
    checks.append(_retrieval_check(case, result))
    checks.append(_specificity_check(result))
    return checks


def _tcode_evidence_check(result: RunResult) -> CheckResult:
    codes = result.sap_research.verified_tcodes
    if not codes:
        return CheckResult(
            id="tcodes_carry_evidence", family="grounding", outcome="skipped",
            detail="no transaction codes were published", must=False,
        )
    missing = [code.tcode for code in codes if not code.source_url]
    return CheckResult(
        id="tcodes_carry_evidence", family="grounding",
        outcome="pass" if not missing else "fail",
        detail=f"{len(codes)} verified" if not missing else f"no source for {missing}",
    )


def _quote_check(result: RunResult) -> CheckResult:
    """Every evidence quote must exist in the page it cites."""
    pages: dict[str, str] = {}
    for source in list(result.sap_research.sources) + list(result.industry_research.sources):
        if source.snippet:
            pages.setdefault(source.url, source.snippet)

    quotes = []
    if result.gap_analysis:
        quotes = [(e.quote, e.url) for gap in result.gap_analysis.gaps for e in gap.evidence]
    elif result.blueprint:
        quotes = [(e.quote, e.url) for d in result.blueprint.design_decisions for e in d.evidence]

    if not quotes:
        return CheckResult(id="quotes_supported", family="grounding", outcome="skipped",
                           detail="no evidence quotes to check", must=False)

    unchecked = [q for q, url in quotes if url not in pages]
    bad = [q[:60] for q, url in quotes if url in pages and not quote_supported(q, pages[url])]
    if bad:
        return CheckResult(id="quotes_supported", family="grounding", outcome="fail",
                           detail=f"{len(bad)} quote(s) not found in the cited page: {bad[:2]}")
    outcome = "pass" if len(unchecked) < len(quotes) else "partial"
    return CheckResult(
        id="quotes_supported", family="grounding", outcome=outcome,
        detail=f"{len(quotes) - len(unchecked)}/{len(quotes)} verifiable from the stored snippets",
        must=False,
    )


def _retrieval_check(case: EvalCase, result: RunResult) -> CheckResult:
    """Did SAP research surface the concepts this process is made of?"""
    if not case.expected_sap_concepts:
        return CheckResult(id="sap_concepts_found", family="grounding", outcome="skipped",
                           detail="no expected concepts declared", must=False)
    haystack = " ".join(
        [result.sap_research.standard.summary]
        + [step.name + " " + step.description for step in result.sap_research.standard.standard_process_flow]
        + [module.name + " " + module.role_in_process for module in result.sap_research.standard.modules]
    ).lower()
    found = [c for c in case.expected_sap_concepts if c.lower() in haystack]
    ratio = len(found) / len(case.expected_sap_concepts)
    return CheckResult(
        id="sap_concepts_found", family="grounding",
        outcome="pass" if ratio >= 0.6 else ("partial" if ratio > 0 else "fail"),
        detail=f"{len(found)}/{len(case.expected_sap_concepts)} concepts: found {found}",
        must=False,
    )


GENERIC_PHRASES = [
    "improve efficiency", "best practice should be followed", "leverage synergies",
    "streamline the process", "enhance the process", "optimise the process",
    "optimize the process", "as appropriate", "where applicable", "industry standard",
]


def _specificity_check(result: RunResult) -> CheckResult:
    """Recommendations should name something, not gesture at improvement."""
    if result.gap_analysis:
        texts = [gap.recommendation for gap in result.gap_analysis.gaps]
    elif result.blueprint:
        texts = [d.recommendation for d in result.blueprint.design_decisions]
    else:
        texts = []
    if not texts:
        return CheckResult(id="recommendations_specific", family="insight", outcome="fail",
                           detail="no recommendations at all")

    vague = [t[:60] for t in texts if any(phrase in t.lower() for phrase in GENERIC_PHRASES)]
    short = [t for t in texts if len(t.split()) < 8]
    problems = len(set(vague)) + len(short)
    ratio = 1 - min(problems / len(texts), 1.0)
    return CheckResult(
        id="recommendations_specific", family="insight",
        outcome="pass" if ratio >= 0.8 else ("partial" if ratio >= 0.5 else "fail"),
        detail=f"{len(texts)} recommendations, {problems} vague or too short",
        must=False,
    )
