"""KPI exclusion for industry benchmark content.

The industry benchmark answers *how leading players run the process*, never
*what number they hit*. Numeric targets and named metrics are removed from all
industry-derived content: they date quickly, rarely survive sourcing scrutiny,
and turn a qualitative design document into an unverifiable scorecard.

The gap analysis inherits the constraint for any field that quotes industry
practice (``Gap.industry_reference``), but SAP process facts and the AS-IS
description are untouched - a step named "Inspect returned part" is allowed to
mention a document number, and an AS-IS pain point may legitimately say the
backlog is large.
"""

from __future__ import annotations

from typing import Any

from ..models import ExclusionRecord, ValidationReport
from ..resources_loader import compile_patterns, load_resource
from .base import PatternFilter

PROMPT_RULE = (
    "NO KPIs OR METRICS. Industry benchmark content must be strictly qualitative. Never "
    "output percentages, ratios, durations, monetary values, quartiles, targets, service "
    "levels, named KPIs (fill rate, OTIF, turnaround time, cycle time, lead time, first-time-"
    "right, cost per claim, inventory turns) or any other measurement. Describe the practice, "
    "the operating model and the enablers - not how they are measured or how good the numbers "
    "are. A sentence that cannot be written without a number should be omitted."
)


class KpiFilter(PatternFilter):
    rule_name = "kpi"

    def __init__(self, resource_dir: str | None = None) -> None:
        data = load_resource("kpi_terms", resource_dir)
        patterns = list(data.get("numeric_patterns", [])) + list(data.get("metric_patterns", []))
        super().__init__(patterns=compile_patterns(patterns))

    def _record(self, value: Any, stage: str, report: ValidationReport) -> tuple[Any, int]:
        cleaned, removals = self.scrub_structure(value)
        for path, text, terms in removals:
            report.exclusions.append(
                ExclusionRecord(
                    stage=stage, location=path or "(root)", excluded_text=text,
                    matched_terms=terms, rule="kpi",
                )
            )
        return cleaned, len(removals)

    def scrub_industry(self, value: Any, stage: str, report: ValidationReport) -> Any:
        """Pre-LLM filtering of retrieved industry material."""
        cleaned, _ = self._record(value, stage, report)
        return cleaned

    def validate_output(self, value: Any, stage: str, report: ValidationReport) -> Any:
        """Post-LLM validation: a hit means the model ignored the KPI rule."""
        cleaned, count = self._record(value, stage, report)
        if count:
            report.warn(
                f"[kpi] {count} KPI-bearing segment(s) removed from industry content at {stage} "
                "after generation."
            )
        return cleaned
