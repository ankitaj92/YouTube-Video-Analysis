"""Typed contracts for every stage of the pipeline.

The extraction models double as the JSON schemas handed to the model through
structured outputs, so every field name here is also a prompt instruction.
"""

from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field

Severity = Literal["critical", "high", "medium", "low"]
Verdict = Literal["best_in_class", "at_par", "improvement_needed", "insufficient_evidence"]
RunMode = Literal["gap", "greenfield"]
SourceTier = Literal[
    "sap_official",
    "industry_credible",
    "scholarly",
    "other",
    "denied",
]


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------
class Source(BaseModel):
    """A retrieved document. Every downstream claim points back to one of these."""

    url: str
    title: str = ""
    domain: str = ""
    tier: SourceTier = "other"
    category: str = ""          # consultancy / standards_body / ... for industry sources
    retrieved_query: str = ""   # which query surfaced it
    snippet: str = ""


class Evidence(BaseModel):
    """A verbatim quote plus the source it came from."""

    quote: str = Field(description="Verbatim sentence from the source that supports the claim.")
    url: str = Field(description="URL of the source containing that exact sentence.")
    source_title: str = ""


# ---------------------------------------------------------------------------
# Stage 1 - current (AS-IS) process
# ---------------------------------------------------------------------------
class ProcessStep(BaseModel):
    seq: int = 0
    name: str
    description: str = ""
    actor: str = ""
    system: str = ""
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    pain_points: list[str] = Field(default_factory=list)


class CurrentProcess(BaseModel):
    process_id: str
    process_name: str
    business_area: str = "Automotive aftersales logistics"
    owner: str = ""
    description: str = ""
    scope_in: list[str] = Field(default_factory=list)
    scope_out: list[str] = Field(default_factory=list)
    source_system: str = "SAP ECC"
    target_system: str = "SAP S/4HANA"
    steps: list[ProcessStep] = Field(default_factory=list)
    systems_involved: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    known_pain_points: list[str] = Field(default_factory=list)
    custom_objects: list[str] = Field(default_factory=list)  # Z-programs, enhancements
    notes: str = ""


# ---------------------------------------------------------------------------
# Stage 2 - SAP standard research
# ---------------------------------------------------------------------------
class SapProcessStep(BaseModel):
    seq: int
    name: str
    description: str = ""
    sap_area: str = Field(default="", description="SAP area this step belongs to, as stated by the source.")
    evidence: list[Evidence] = Field(default_factory=list)


class SapModule(BaseModel):
    name: str = Field(description="Module / component name exactly as the SAP source writes it.")
    role_in_process: str = ""
    evidence: list[Evidence] = Field(default_factory=list)


class TCodeCandidate(BaseModel):
    """A T-code the model believes it saw. Not trusted until verified."""

    tcode: str
    purpose: str = ""
    sap_area: str = ""
    source_url: str = ""
    supporting_quote: str = ""


class VerifiedTCode(BaseModel):
    """A T-code that a public SAP page provably contains."""

    tcode: str
    purpose: str = ""
    sap_area: str = ""
    source_url: str
    source_title: str = ""
    supporting_quote: str = ""
    verification: str = "literal match on public SAP source"


class DroppedTCode(BaseModel):
    tcode: str
    source_url: str = ""
    reason: str


class SapStandardProcess(BaseModel):
    process_name: str
    summary: str = ""
    standard_process_flow: list[SapProcessStep] = Field(default_factory=list)
    modules: list[SapModule] = Field(default_factory=list)
    tcodes: list[TCodeCandidate] = Field(default_factory=list)
    master_data_objects: list[str] = Field(default_factory=list)
    integration_points: list[str] = Field(default_factory=list)
    s4_specific_changes: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


class SapResearchResult(BaseModel):
    standard: SapStandardProcess
    verified_tcodes: list[VerifiedTCode] = Field(default_factory=list)
    dropped_tcodes: list[DroppedTCode] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    queries_used: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Stage 3 - industry benchmark research (KPI-free by contract)
# ---------------------------------------------------------------------------
class IndustryPractice(BaseModel):
    """A qualitative practice. No numbers, no targets, no measurement language."""

    title: str
    description: str = Field(description="Qualitative description of how leading players run this.")
    maturity: Literal["emerging", "established", "leading"] = "established"
    applies_to_step: str = ""
    evidence: list[Evidence] = Field(default_factory=list)


class ScholarlyWork(BaseModel):
    title: str
    year: Optional[int] = None
    venue: str = ""
    doi: str = ""
    url: str = ""
    abstract_excerpt: str = ""
    relevance: str = ""


class IndustryBenchmark(BaseModel):
    process_name: str
    summary: str = ""
    leading_practices: list[IndustryPractice] = Field(default_factory=list)
    common_operating_models: list[str] = Field(default_factory=list)
    automation_and_digital_enablers: list[str] = Field(default_factory=list)
    risks_and_failure_modes: list[str] = Field(default_factory=list)
    scholarly_works: list[ScholarlyWork] = Field(default_factory=list)


class IndustryResearchResult(BaseModel):
    benchmark: IndustryBenchmark
    sources: list[Source] = Field(default_factory=list)
    rejected_sources: list[Source] = Field(default_factory=list)
    queries_used: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Stage 4 - gap analysis
# ---------------------------------------------------------------------------
GapDimension = Literal[
    "process_flow",
    "system_and_module_fit",
    "master_data",
    "automation_and_digital",
    "dealer_experience",
    "compliance_and_traceability",
    "integration",
    "organisation_and_roles",
]

Disposition = Literal[
    "fit_to_standard",
    "configure_in_standard",
    "extend_with_side_by_side",
    "keep_custom_justified",
    "retire",
]


class Gap(BaseModel):
    gap_id: str
    dimension: GapDimension
    title: str
    current_state: str = Field(description="What the AS-IS process does today.")
    sap_standard_reference: str = Field(description="What SAP standard offers, grounded in the SAP research only.")
    industry_reference: str = Field(default="", description="Qualitative leading practice. No metrics.")
    gap_description: str
    severity: Severity
    recommendation: str
    s4_disposition: Disposition
    effort: Literal["S", "M", "L", "XL"] = "M"
    dependencies: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)


class ToBeStep(BaseModel):
    seq: int
    name: str
    description: str = ""
    system: str = ""
    actor: str = ""
    change_vs_current: str = ""


class GapAnalysis(BaseModel):
    process_name: str
    executive_summary: str
    verdict: Verdict
    verdict_rationale: str
    gaps: list[Gap] = Field(default_factory=list)
    to_be_process: list[ToBeStep] = Field(default_factory=list)
    quick_wins: list[str] = Field(default_factory=list)
    roadmap_phases: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Process provenance - where the AS-IS came from, or why there wasn't one
# ---------------------------------------------------------------------------
class PageCandidate(BaseModel):
    title: str
    url: str = ""
    space_key: str = ""
    score: float = 0.0
    page_id: str = ""


class ProcessProvenance(BaseModel):
    """Auditable record of how the current process was (or was not) found."""

    source: Literal["confluence", "yaml", "none"] = "yaml"
    reference: str = ""            # human-readable citation
    url: str = ""
    page_id: str = ""
    space_key: str = ""
    version: int = 0
    last_modified: str = ""
    last_modified_by: str = ""
    match_score: float = 0.0
    searched_for: str = ""
    spaces_searched: list[str] = Field(default_factory=list)
    candidates: list[PageCandidate] = Field(default_factory=list)
    not_found_reason: str = ""


# ---------------------------------------------------------------------------
# Greenfield design - used when the process does not exist yet
# ---------------------------------------------------------------------------
class DesignDecision(BaseModel):
    decision: str = Field(description="The design question being settled.")
    options_considered: list[str] = Field(default_factory=list)
    recommendation: str = ""
    rationale: str = ""
    sap_basis: str = Field(default="", description="What the SAP record supports. Empty if it does not.")
    industry_basis: str = Field(default="", description="Qualitative leading practice. No metrics.")
    evidence: list[Evidence] = Field(default_factory=list)


class RoleResponsibility(BaseModel):
    role: str
    responsibilities: list[str] = Field(default_factory=list)


class ProcessBlueprint(BaseModel):
    """An implementation proposal for a process that does not exist today."""

    process_name: str
    summary: str
    why_new: str = Field(default="", description="Why this is being designed from scratch.")
    design_principles: list[str] = Field(default_factory=list)
    recommended_flow: list["ToBeStep"] = Field(default_factory=list)
    design_decisions: list[DesignDecision] = Field(default_factory=list)
    configuration_scope: list[str] = Field(default_factory=list)
    master_data_prerequisites: list[str] = Field(default_factory=list)
    roles_and_responsibilities: list[RoleResponsibility] = Field(default_factory=list)
    integration_points: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    roadmap_phases: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Validation + run record
# ---------------------------------------------------------------------------
class ExclusionRecord(BaseModel):
    stage: str
    location: str
    excluded_text: str
    matched_terms: list[str] = Field(default_factory=list)
    rule: Literal["tolerance", "kpi", "denied_source", "ungrounded_tcode", "unverified_quote"] = "tolerance"


class ValidationReport(BaseModel):
    passed: bool = True
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    exclusions: list[ExclusionRecord] = Field(default_factory=list)

    def error(self, message: str) -> None:
        self.errors.append(message)
        self.passed = False

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def extend(self, other: "ValidationReport") -> None:
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)
        self.exclusions.extend(other.exclusions)
        self.passed = self.passed and other.passed


class RunResult(BaseModel):
    process_id: str
    process_name: str
    run_date: date
    mode: RunMode = "gap"
    provenance: ProcessProvenance = Field(default_factory=ProcessProvenance)
    current_process: Optional[CurrentProcess] = None
    sap_research: SapResearchResult
    industry_research: IndustryResearchResult
    gap_analysis: Optional[GapAnalysis] = None
    blueprint: Optional[ProcessBlueprint] = None
    validation: ValidationReport
    offline: bool = False
