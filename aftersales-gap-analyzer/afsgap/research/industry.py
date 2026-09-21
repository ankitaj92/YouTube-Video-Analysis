"""Industry benchmark research.

Three deliberate properties:

* **Broad, multi-variant search.** The process name alone is a poor query - it
  is internal vocabulary. Queries are generated across several framings (generic
  process family, reverse logistics, dealer/OEM operating model, digital
  enablers, quality and warranty context) so the search is not hostage to one
  phrasing.
* **Search normally, then filter.** The web search runs unrestricted; results
  are filtered against the credible-source allowlist *afterwards*, and rejected
  sources are kept in the run record so the filtering is auditable.
* **Scholarly leg.** OpenAlex is queried in parallel for peer-reviewed work.

Everything the stage emits is KPI-free by contract.
"""

from __future__ import annotations

import logging

from ..config import Settings
from ..filters.kpi import KpiFilter
from ..filters.sources import SourceClassifier
from ..filters.tolerance import ToleranceFilter
from ..llm import ClaudeClient
from ..net import build_session
from ..models import (
    CurrentProcess,
    IndustryBenchmark,
    IndustryResearchResult,
    ScholarlyWork,
    Source,
    ValidationReport,
)
from ..prompts import INDUSTRY_EXTRACT_SYSTEM, INDUSTRY_RESEARCH_SYSTEM
from .openalex import search_works
from .quotes import verify_evidence

logger = logging.getLogger(__name__)

# Query variants. {p} = process name. Each line reframes the question rather
# than repeating it - this replaces the old single combined multi-domain query.
WEB_QUERY_VARIANTS = [
    "{p} best practice automotive aftersales",
    "automotive OEM spare parts reverse logistics leading practice",
    "automotive dealer returns handling operating model",
    "aftermarket parts returns process design automotive manufacturer",
    "automotive warranty and claims process leading practice dealer network",
    "service parts supply chain operating model automotive OEM",
    "reverse logistics automotive industry practice study",
    "dealer self service portal parts returns digitalisation automotive",
    "automotive aftersales logistics process automation practice",
    "spare parts distribution network design automotive aftermarket",
]

SCHOLARLY_QUERY_VARIANTS = [
    "automotive reverse logistics spare parts returns",
    "aftersales service parts supply chain management automotive",
    "warranty claims management automotive manufacturer process",
    "closed loop supply chain automotive remanufacturing core returns",
    "{p} automotive supply chain",
]


def build_industry_queries(process: CurrentProcess) -> tuple[list[str], list[str]]:
    name = process.process_name
    web = [variant.format(p=name) for variant in WEB_QUERY_VARIANTS]
    scholarly = [variant.format(p=name) for variant in SCHOLARLY_QUERY_VARIANTS]
    return web, scholarly


class IndustryResearcher:
    def __init__(
        self,
        client: ClaudeClient,
        settings: Settings,
        classifier: SourceClassifier,
        tolerance: ToleranceFilter,
        kpi: KpiFilter,
    ) -> None:
        self.client = client
        self.settings = settings
        self.classifier = classifier
        self.tolerance = tolerance
        self.kpi = kpi
        self.session = build_session(settings)

    def run(self, process: CurrentProcess, report: ValidationReport) -> IndustryResearchResult:
        web_queries, scholarly_queries = build_industry_queries(process)
        scholarly = self._scholarly(scholarly_queries, report)

        prompt = (
            f"Process under study: {process.process_name}\n"
            f"Context: {process.business_area}. {process.description}\n\n"
            "Research how leading automotive manufacturers, dealer networks and logistics "
            "partners run this kind of process. Run several differently-framed searches rather "
            "than one narrow query on the process name.\n\n"
            "QUERY VARIANTS TO RUN (adapt and extend):\n"
            + "\n".join(f"- {query}" for query in web_queries)
            + "\n\nPEER-REVIEWED MATERIAL ALREADY RETRIEVED (OpenAlex - use it, cite it by URL):\n"
            + self._scholarly_block(scholarly)
            + "\n\nDeliver: leading practices, common operating models, automation and digital "
            "enablers, and recurring failure modes. Qualitative only."
        )

        transcript = self.client.research(
            system=INDUSTRY_RESEARCH_SYSTEM,
            prompt=prompt,
            queries=web_queries,
            max_searches=self.settings.industry_max_searches,
        )

        # 1. classify everything the unrestricted search returned
        all_sources = self._collect_sources(transcript)
        kept, rejected = self.classifier.split_industry(all_sources)
        if not kept:
            report.warn(
                "[industry] no source passed the credibility allowlist; the benchmark rests on "
                "the scholarly leg alone."
            )

        # 2. pre-LLM filtering: tolerance out, KPIs out, non-credible sources out
        evidence = self._credible_evidence_block(transcript, kept, scholarly)
        evidence = self.tolerance.scrub_input(evidence, "industry_research.evidence", report)
        evidence = self.kpi.scrub_industry(evidence, "industry_research.evidence", report)

        benchmark = self.client.extract(
            system=INDUSTRY_EXTRACT_SYSTEM,
            prompt=(
                f"Process under study: {process.process_name}\n\n{evidence}\n\n"
                "Produce the structured, strictly qualitative industry benchmark."
            ),
            schema=IndustryBenchmark,
        )

        # 3. post-LLM validation: both exclusions re-applied to generated content
        payload = self.tolerance.validate_output(benchmark.model_dump(), "industry_research.output", report)
        payload = self.kpi.validate_output(payload, "industry_research.output", report)
        if self.settings.verify_quotes:
            payload = verify_evidence(payload, transcript.pages, "industry_research.evidence", report)
        benchmark = IndustryBenchmark.model_validate(payload)
        if not benchmark.scholarly_works:
            benchmark.scholarly_works = scholarly[:6]

        return IndustryResearchResult(
            benchmark=benchmark,
            sources=kept,
            rejected_sources=rejected,
            queries_used=transcript.queries or web_queries,
        )

    # ------------------------------------------------------------------
    def _scholarly(self, queries: list[str], report: ValidationReport) -> list[ScholarlyWork]:
        works: list[ScholarlyWork] = []
        seen: set[str] = set()
        if getattr(self.client, "offline", False):
            works = list(self.client.scholarly_works())
            queries = []
        elif not self.settings.openalex_enabled:
            report.warn("[industry] OpenAlex is disabled; the scholarly leg was skipped.")
            queries = []
        for query in queries:
            for work in search_works(
                query,
                per_page=self.settings.openalex_per_page,
                from_year=self.settings.openalex_from_year,
                mailto=self.settings.openalex_mailto,
                timeout=self.settings.http_timeout,
                session=self.session,
            ):
                key = (work.doi or work.url or work.title).lower()
                if key in seen:
                    continue
                seen.add(key)
                works.append(work)
        if not works and queries:
            report.warn("[industry] OpenAlex returned no scholarly works.")
        # scrub abstracts before they ever reach a prompt
        cleaned: list[ScholarlyWork] = []
        for work in works:
            payload = self.tolerance.scrub_input(work.model_dump(), "industry_research.openalex", report)
            payload = self.kpi.scrub_industry(payload, "industry_research.openalex", report)
            cleaned.append(ScholarlyWork.model_validate(payload))
        return cleaned

    def _scholarly_block(self, works: list[ScholarlyWork]) -> str:
        if not works:
            return "  (none retrieved)"
        lines = []
        for work in works[:12]:
            lines.append(f"- {work.title} ({work.year or 'n.d.'}, {work.venue or 'unknown venue'}) {work.url}")
            if work.abstract_excerpt:
                lines.append(f"    ABSTRACT: {work.abstract_excerpt[:500]}")
        return "\n".join(lines)

    def _credible_evidence_block(self, transcript, kept: list[Source], scholarly: list[ScholarlyWork]) -> str:
        credible_urls = {source.url for source in kept}
        lines = ["## Credible sources retained after allowlist filtering", ""]
        for source in kept:
            lines.append(f"- [{source.category}] {source.title or source.domain} :: {source.url}")
        lines += ["", "## Verbatim citations from credible sources only", ""]
        for citation in transcript.citations:
            if citation.url in credible_urls or self.classifier.is_credible_industry(citation.url):
                lines.append(f'- "{citation.cited_text}" -- {citation.url}')
        lines += ["", "## Peer-reviewed works (OpenAlex)", "", self._scholarly_block(scholarly)]
        lines += ["", "## Research narrative", "", transcript.text]
        return "\n".join(lines)

    def _collect_sources(self, transcript) -> list[Source]:
        seen: set[str] = set()
        sources: list[Source] = []
        for hit in transcript.hits:
            if hit.url in seen:
                continue
            seen.add(hit.url)
            sources.append(self.classifier.describe(hit.url, hit.title, hit.query, hit.snippet))
        for citation in transcript.citations:
            if citation.url in seen:
                continue
            seen.add(citation.url)
            sources.append(self.classifier.describe(citation.url, citation.title, snippet=citation.cited_text))
        return sources
