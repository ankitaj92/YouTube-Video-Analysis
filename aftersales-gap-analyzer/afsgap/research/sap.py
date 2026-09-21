"""SAP standard research.

Produces the process flow, the relevant SAP modules and the transaction codes
for a process - all restricted to official public SAP domains, all carrying the
URL they came from, and with every T-code independently verified afterwards.
"""

from __future__ import annotations

import logging

from ..config import Settings
from ..filters.sources import SourceClassifier
from ..filters.tolerance import ToleranceFilter
from ..llm import ClaudeClient
from ..net import build_session
from ..models import (
    CurrentProcess,
    DroppedTCode,
    ExclusionRecord,
    SapResearchResult,
    SapStandardProcess,
    Source,
    ValidationReport,
)
from ..prompts import SAP_EXTRACT_SYSTEM, SAP_RESEARCH_SYSTEM
from ..resources_loader import load_resource
from .quotes import verify_evidence
from .tcode import TCodeVerifier

logger = logging.getLogger(__name__)


def build_sap_queries(process: CurrentProcess, resource_dir: str | None = None) -> list[str]:
    """Query set = the process itself, crossed with the SAP area hints.

    The hints (SD, EWM, CMH, master data, dealer front-end/SOp, aftersales) only
    shape *what we search for*. Nothing here is treated as a fact about SAP.
    """
    data = load_resource("sap_area_hints", resource_dir)
    name = process.process_name
    queries = [
        f"SAP S/4HANA {name} standard process flow",
        f"SAP S/4HANA {name} configuration guide",
        f"SAP {name} transaction code",
        f"SAP S/4HANA automotive aftersales {name}",
    ]
    for area in data.get("areas", []):
        for term in area.get("search_terms", []):
            queries.append(term)
    # de-duplicate, keep order
    seen: set[str] = set()
    unique: list[str] = []
    for query in queries:
        key = query.lower()
        if key not in seen:
            seen.add(key)
            unique.append(query)
    return unique


def _area_hint_block(resource_dir: str | None = None) -> str:
    data = load_resource("sap_area_hints", resource_dir)
    lines = []
    for area in data.get("areas", []):
        lines.append(f"- {area.get('label')}: {area.get('relevance', '')}")
        for term in area.get("search_terms", []):
            lines.append(f"    suggested query: {term}")
    return "\n".join(lines)


def _process_brief(process: CurrentProcess) -> str:
    steps = "\n".join(
        f"  {step.seq}. {step.name} ({step.actor or 'unspecified actor'}, {step.system or 'unspecified system'}): {step.description}"
        for step in process.steps
    )
    return (
        f"Process name: {process.process_name}\n"
        f"Business area: {process.business_area}\n"
        f"Current system: {process.source_system} -> target: {process.target_system}\n"
        f"Description: {process.description}\n"
        f"Current steps:\n{steps or '  (none supplied)'}\n"
    )


def _exclusion(item: DroppedTCode) -> ExclusionRecord:
    return ExclusionRecord(
        stage="sap_research.tcode",
        location=item.tcode,
        excluded_text=f"{item.tcode} <- {item.source_url or 'no source'}",
        matched_terms=[item.reason],
        rule="ungrounded_tcode",
    )


class SapResearcher:
    def __init__(
        self,
        client: ClaudeClient,
        settings: Settings,
        classifier: SourceClassifier,
        tolerance: ToleranceFilter,
    ) -> None:
        self.client = client
        self.settings = settings
        self.classifier = classifier
        self.tolerance = tolerance
        self.verifier = TCodeVerifier(
            classifier=classifier,
            cache_dir=settings.cache_dir,
            timeout=settings.http_timeout,
            require_literal_evidence=settings.require_literal_tcode_evidence,
            session=build_session(settings),
        )

    def run(self, process: CurrentProcess, report: ValidationReport) -> SapResearchResult:
        queries = build_sap_queries(process)
        prompt = (
            f"{_process_brief(process)}\n"
            "Research how SAP's own public documentation describes the standard process that "
            "corresponds to this one, for an SAP S/4HANA target.\n\n"
            "Deliver: (a) the standard process flow, (b) the relevant SAP modules/components, "
            "(c) transaction codes you can quote verbatim from a retrieved SAP page, "
            "(d) master data objects, (e) integration points, (f) ECC-to-S/4HANA differences.\n\n"
            "SEARCH GUIDANCE (areas to probe - these are hints for query construction only, "
            "not statements about this process):\n"
            f"{_area_hint_block()}\n\n"
            "SUGGESTED QUERIES (adapt freely, and add your own):\n"
            + "\n".join(f"- {query}" for query in queries[:24])
        )

        transcript = self.client.research(
            system=SAP_RESEARCH_SYSTEM,
            prompt=prompt,
            queries=queries,
            allowed_domains=self.classifier.sap_official,
            max_searches=self.settings.sap_max_searches,
        )

        # Pre-LLM filtering of retrieved material, before it reaches extraction.
        evidence = self.tolerance.scrub_input(transcript.evidence_block(), "sap_research.evidence", report)

        sources = self._collect_sources(transcript)
        if not sources:
            report.warn("[sap] no official SAP sources were retrieved; SAP findings will be thin.")

        standard = self.client.extract(
            system=SAP_EXTRACT_SYSTEM,
            prompt=(
                f"Process under study: {process.process_name}\n\n{evidence}\n\n"
                "Produce the structured SAP standard record. Only include a transaction code when "
                "the quote you attach contains that code and the URL is an official SAP domain."
            ),
            schema=SapStandardProcess,
        )

        # Post-LLM validation: tolerance content never survives generation, and
        # a quote only counts as evidence if the page it cites contains it.
        payload = self.tolerance.validate_output(standard.model_dump(), "sap_research.output", report)
        if self.settings.verify_quotes:
            payload = verify_evidence(payload, transcript.pages, "sap_research.evidence", report)
        standard = SapStandardProcess.model_validate(payload)

        verified, dropped = self.verifier.verify(standard.tcodes)
        for item in dropped:
            report.exclusions.append(_exclusion(item))
        if dropped and not verified:
            report.warn("[sap] every proposed transaction code failed verification and was dropped.")

        return SapResearchResult(
            standard=standard,
            verified_tcodes=verified,
            dropped_tcodes=dropped,
            sources=sources,
            queries_used=transcript.queries or queries[:12],
        )

    def _collect_sources(self, transcript) -> list[Source]:
        seen: set[str] = set()
        sources: list[Source] = []
        for hit in transcript.hits:
            if hit.url in seen:
                continue
            seen.add(hit.url)
            described = self.classifier.describe(hit.url, hit.title, hit.query, hit.snippet)
            if described.tier == "sap_official":
                sources.append(described)
        for citation in transcript.citations:
            if citation.url in seen:
                continue
            seen.add(citation.url)
            described = self.classifier.describe(citation.url, citation.title, snippet=citation.cited_text)
            if described.tier == "sap_official":
                sources.append(described)
        return sources
