"""Typed contracts for the eval set.

Scores are reported in three families and never averaged into one number,
because they mean different things:

* **compliance** - deterministic rules the output must obey (no tolerance
  content, no unverified transaction code, industry content KPI-free). Anything
  below 100% is a defect, not a lower score.
* **grounding** - whether findings rest on retrieved evidence: sources on the
  allowlist, quotes present in the pages they cite, SAP claims traceable.
* **insight** - whether the analysis is actually useful: does it find the
  problems the business already knows it has, dispose of the custom objects,
  and make specific rather than generic recommendations.

A single blended score would let good prose hide a compliance breach.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

Family = Literal["compliance", "grounding", "insight"]
Outcome = Literal["pass", "fail", "partial", "skipped"]


class RubricItem(BaseModel):
    """One thing the analysis is expected to do."""

    id: str
    family: Family = "insight"
    description: str
    # Cheap pre-check: if any keyword appears the item is likely addressed.
    # Used on its own when no judge is configured.
    keywords: list[str] = Field(default_factory=list)
    must: bool = True          # a `must` failure blocks the case
    source: Literal["authored", "derived_pain_point", "derived_custom_object"] = "authored"


class EvalCase(BaseModel):
    """One scenario in the eval set."""

    id: str
    description: str = ""
    # What to analyse: a process name (Confluence lookup) or a YAML path.
    target: str
    mode: Literal["gap", "greenfield", "any"] = "any"
    tags: list[str] = Field(default_factory=list)

    # How to run it. Offline cases are deterministic and cost nothing.
    offline: bool = False
    force_new: bool = False
    skip_industry: bool = False

    # Hand-authored expectations, on top of the ones derived from the process
    # definition. Keep these few and specific.
    rubric: list[RubricItem] = Field(default_factory=list)

    # Statements the analysis must NOT make.
    forbidden: list[str] = Field(default_factory=list)

    # Concepts the SAP research should surface, if it retrieved anything useful.
    expected_sap_concepts: list[str] = Field(default_factory=list)

    enabled: bool = True


class CheckResult(BaseModel):
    id: str
    family: Family
    outcome: Outcome
    detail: str = ""
    must: bool = True


class CaseScore(BaseModel):
    compliance: float = 0.0
    grounding: float = 0.0
    insight: float = 0.0
    blocking_failures: int = 0

    def summary(self) -> str:
        return (
            f"compliance {self.compliance:5.1%}  grounding {self.grounding:5.1%}  "
            f"insight {self.insight:5.1%}"
        )


class CaseResult(BaseModel):
    case_id: str
    ran: bool = True
    error: str = ""
    mode: str = ""
    duration_seconds: float = 0.0
    checks: list[CheckResult] = Field(default_factory=list)
    score: CaseScore = Field(default_factory=CaseScore)
    run_json: str = ""            # path to the pipeline's run record
    judged: bool = False

    def failures(self, family: Optional[Family] = None) -> list[CheckResult]:
        return [
            check for check in self.checks
            if check.outcome == "fail" and (family is None or check.family == family)
        ]


class EvalRun(BaseModel):
    label: str = ""
    started_at: datetime = Field(default_factory=datetime.now)
    backend: str = ""
    model: str = ""
    judge: str = "none"
    cases: list[CaseResult] = Field(default_factory=list)

    def aggregate(self) -> CaseScore:
        ran = [case for case in self.cases if case.ran]
        if not ran:
            return CaseScore()
        return CaseScore(
            compliance=sum(c.score.compliance for c in ran) / len(ran),
            grounding=sum(c.score.grounding for c in ran) / len(ran),
            insight=sum(c.score.insight for c in ran) / len(ran),
            blocking_failures=sum(c.score.blocking_failures for c in ran),
        )
