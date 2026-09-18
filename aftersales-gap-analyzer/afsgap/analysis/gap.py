"""Three-way gap analysis: AS-IS vs SAP standard vs industry practice."""

from __future__ import annotations

import json
import logging

from ..filters.kpi import KpiFilter
from ..filters.tolerance import ToleranceFilter
from ..llm import ClaudeClient
from ..models import (
    CurrentProcess,
    GapAnalysis,
    IndustryResearchResult,
    SapResearchResult,
    ValidationReport,
)
from ..prompts import GAP_ANALYSIS_SYSTEM

logger = logging.getLogger(__name__)


class GapAnalyzer:
    def __init__(self, client: ClaudeClient, tolerance: ToleranceFilter, kpi: KpiFilter) -> None:
        self.client = client
        self.tolerance = tolerance
        self.kpi = kpi

    def run(
        self,
        process: CurrentProcess,
        sap: SapResearchResult,
        industry: IndustryResearchResult,
        report: ValidationReport,
    ) -> GapAnalysis:
        sap_payload = {
            "standard_process_flow": [step.model_dump() for step in sap.standard.standard_process_flow],
            "modules": [module.model_dump() for module in sap.standard.modules],
            "verified_transaction_codes": [code.model_dump() for code in sap.verified_tcodes],
            "transaction_codes_rejected_for_lack_of_evidence": [
                {"tcode": code.tcode, "reason": code.reason} for code in sap.dropped_tcodes
            ],
            "master_data_objects": sap.standard.master_data_objects,
            "integration_points": sap.standard.integration_points,
            "s4_specific_changes": sap.standard.s4_specific_changes,
            "open_questions": sap.standard.open_questions,
            "sources": [source.url for source in sap.sources],
        }
        industry_payload = {
            "summary": industry.benchmark.summary,
            "leading_practices": [practice.model_dump() for practice in industry.benchmark.leading_practices],
            "common_operating_models": industry.benchmark.common_operating_models,
            "automation_and_digital_enablers": industry.benchmark.automation_and_digital_enablers,
            "risks_and_failure_modes": industry.benchmark.risks_and_failure_modes,
            "scholarly_works": [work.model_dump() for work in industry.benchmark.scholarly_works],
            "sources": [source.url for source in industry.sources],
        }

        prompt = (
            "## 1. Current (AS-IS) process\n"
            f"{json.dumps(process.model_dump(), indent=2, ensure_ascii=False)}\n\n"
            "## 2. SAP standard record (official SAP sources only - do not add SAP facts)\n"
            f"{json.dumps(sap_payload, indent=2, ensure_ascii=False)}\n\n"
            "## 3. Industry practice benchmark (qualitative, KPI-free - keep it that way)\n"
            f"{json.dumps(industry_payload, indent=2, ensure_ascii=False)}\n\n"
            "Produce the gap analysis, the TO-BE flow, the verdict and the roadmap. Reference a "
            "transaction code only if it appears in verified_transaction_codes. Where the SAP "
            "record is thin, say so in assumptions rather than filling the gap from memory."
        )

        analysis = self.client.extract(system=GAP_ANALYSIS_SYSTEM, prompt=prompt, schema=GapAnalysis)

        payload = self.tolerance.validate_output(analysis.model_dump(), "gap_analysis.output", report)
        # KPI rule applies to the industry-derived fields of each gap only.
        for gap in payload.get("gaps", []):
            if gap.get("industry_reference"):
                gap["industry_reference"] = self.kpi.validate_output(
                    gap["industry_reference"], "gap_analysis.industry_reference", report
                )
        analysis = GapAnalysis.model_validate(payload)
        self._check_tcode_grounding(analysis, sap, report)
        return analysis

    def _check_tcode_grounding(self, analysis: GapAnalysis, sap: SapResearchResult, report: ValidationReport) -> None:
        """Nothing may cite a transaction code that verification did not clear."""
        from ..research.tcode import find_tcode_mentions

        verified = {code.tcode.upper() for code in sap.verified_tcodes}
        rejected = {code.tcode.upper() for code in sap.dropped_tcodes}
        fields: list[str] = [analysis.executive_summary, analysis.verdict_rationale]
        for gap in analysis.gaps:
            fields += [gap.sap_standard_reference, gap.recommendation, gap.gap_description]
        for step in analysis.to_be_process:
            fields += [step.description, step.system]

        mentioned = find_tcode_mentions("\n".join(f for f in fields if f), also_search_for=rejected)
        for candidate in sorted(mentioned - verified):
            report.warn(
                f"[tcode] '{candidate}' appears in the gap analysis but is not in the verified "
                "transaction-code list - treat it as unconfirmed."
            )
