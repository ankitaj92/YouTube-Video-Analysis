"""Greenfield design: propose a process that does not exist yet.

Same evidence discipline as the gap analysis - SAP facts come only from the SAP
record, industry statements stay qualitative, and tolerance topics never appear.
The difference is the absence of an AS-IS: there is nothing to compare against,
so the output is a design proposal with its decisions made explicit.
"""

from __future__ import annotations

import json
import logging

from ..filters.kpi import KpiFilter
from ..filters.tolerance import ToleranceFilter
from ..models import (
    IndustryResearchResult,
    ProcessBlueprint,
    ProcessProvenance,
    SapResearchResult,
    ValidationReport,
)
from ..prompts import BLUEPRINT_SYSTEM

logger = logging.getLogger(__name__)


class BlueprintDesigner:
    def __init__(self, client, tolerance: ToleranceFilter, kpi: KpiFilter) -> None:
        self.client = client
        self.tolerance = tolerance
        self.kpi = kpi

    def run(
        self,
        process_name: str,
        provenance: ProcessProvenance,
        sap: SapResearchResult,
        industry: IndustryResearchResult,
        report: ValidationReport,
    ) -> ProcessBlueprint:
        sap_payload = {
            "standard_process_flow": [step.model_dump() for step in sap.standard.standard_process_flow],
            "modules": [module.model_dump() for module in sap.standard.modules],
            "verified_transaction_codes": [code.model_dump() for code in sap.verified_tcodes],
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
            "sources": [source.url for source in industry.sources],
        }

        prompt = (
            f"## Process to design: {process_name}\n"
            "Context: automotive aftersales logistics, SAP S/4HANA target.\n\n"
            "## Why there is no AS-IS\n"
            f"{provenance.not_found_reason or 'No existing process documentation was found.'}\n"
            f"Searched for: '{provenance.searched_for}'"
            + (f" in spaces {provenance.spaces_searched}" if provenance.spaces_searched else "")
            + "\n"
            + (
                "Closest documentation candidates that were rejected: "
                + "; ".join(f"{c.title} (score {c.score})" for c in provenance.candidates[:5])
                + "\n"
                if provenance.candidates
                else ""
            )
            + "\n## SAP standard record (official SAP sources only - do not add SAP facts)\n"
            f"{json.dumps(sap_payload, indent=2, ensure_ascii=False)}\n\n"
            "## Industry practice benchmark (qualitative, KPI-free - keep it that way)\n"
            f"{json.dumps(industry_payload, indent=2, ensure_ascii=False)}\n\n"
            "Produce the implementation blueprint: the recommended flow, the design decisions "
            "behind it with their alternatives, configuration scope, master data prerequisites, "
            "roles, integration points, risks, phasing and open questions."
        )

        blueprint = self.client.extract(system=BLUEPRINT_SYSTEM, prompt=prompt, schema=ProcessBlueprint)

        payload = self.tolerance.validate_output(blueprint.model_dump(), "blueprint.output", report)
        for decision in payload.get("design_decisions", []):
            if decision.get("industry_basis"):
                decision["industry_basis"] = self.kpi.validate_output(
                    decision["industry_basis"], "blueprint.industry_basis", report
                )
        blueprint = ProcessBlueprint.model_validate(payload)
        if not blueprint.process_name:
            blueprint.process_name = process_name
        self._check_tcode_grounding(blueprint, sap, report)
        return blueprint

    def _check_tcode_grounding(
        self, blueprint: ProcessBlueprint, sap: SapResearchResult, report: ValidationReport
    ) -> None:
        from ..research.tcode import find_tcode_mentions

        verified = {code.tcode.upper() for code in sap.verified_tcodes}
        rejected = {code.tcode.upper() for code in sap.dropped_tcodes}
        fields = [blueprint.summary, blueprint.why_new]
        fields += blueprint.configuration_scope + blueprint.integration_points
        for step in blueprint.recommended_flow:
            fields += [step.description, step.system, step.change_vs_current]
        for decision in blueprint.design_decisions:
            fields += [decision.recommendation, decision.rationale, decision.sap_basis]

        mentioned = find_tcode_mentions("\n".join(f for f in fields if f), also_search_for=rejected)
        for candidate in sorted(mentioned - verified):
            report.warn(
                f"[tcode] '{candidate}' appears in the blueprint but is not in the verified "
                "transaction-code list - treat it as unconfirmed."
            )
