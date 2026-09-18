"""Tolerance exclusion.

Tolerance topics (delivery/receipt tolerances, tolerance keys, groups, limits,
over- and under-delivery) are out of scope for this programme. The exclusion is
enforced four times:

1. ``pre``    - the AS-IS process input is scrubbed before any prompt is built.
2. ``pre``    - every retrieved search snippet is scrubbed before extraction.
3. ``prompt`` - :data:`PROMPT_RULE` is embedded in every system prompt.
4. ``post``   - every model output and the rendered document are re-scanned;
                surviving segments are removed and recorded.
"""

from __future__ import annotations

from typing import Any

from ..models import ExclusionRecord, ValidationReport
from ..resources_loader import compile_patterns, load_resource
from .base import PatternFilter

PROMPT_RULE = (
    "HARD EXCLUSION - TOLERANCES. Tolerance topics are entirely out of scope for this "
    "programme. Never mention, research, infer, recommend or reference: tolerances of any "
    "kind, tolerance keys/groups/limits/profiles, quantity or price or date tolerances, "
    "over-delivery, under-delivery, over-receipt, under-receipt, or unlimited over-delivery. "
    "If a source discusses tolerances, ignore that part of the source and use the rest. "
    "Do not substitute synonyms or paraphrases to work around this rule - simply omit the "
    "topic and continue with the remaining content."
)


class ToleranceFilter(PatternFilter):
    rule_name = "tolerance"

    def __init__(self, resource_dir: str | None = None) -> None:
        data = load_resource("tolerance_terms", resource_dir)
        super().__init__(
            patterns=compile_patterns(data.get("patterns", [])),
            allowances=compile_patterns(data.get("allowances", [])),
        )

    # -- convenience wrappers used by the pipeline stages -------------------
    def scrub_input(self, value: Any, stage: str, report: ValidationReport) -> Any:
        """Pre-LLM filtering. Removals are recorded, never an error."""
        cleaned, removals = self.scrub_structure(value)
        for path, text, terms in removals:
            report.exclusions.append(
                ExclusionRecord(
                    stage=stage, location=path or "(root)", excluded_text=text,
                    matched_terms=terms, rule="tolerance",
                )
            )
        return cleaned

    def validate_output(self, value: Any, stage: str, report: ValidationReport) -> Any:
        """Post-LLM validation.

        A hit here means the model ignored :data:`PROMPT_RULE`, so it is scrubbed
        *and* raised as a warning - the run stays usable but the breach is visible
        in the validation log of the design document.
        """
        cleaned, removals = self.scrub_structure(value)
        for path, text, terms in removals:
            report.exclusions.append(
                ExclusionRecord(
                    stage=stage, location=path or "(root)", excluded_text=text,
                    matched_terms=terms, rule="tolerance",
                )
            )
            report.warn(
                f"[tolerance] model output at {stage}:{path or '(root)'} contained excluded "
                f"terms {terms} - segment removed post-generation."
            )
        return cleaned

    def assert_clean(self, text: str, stage: str, report: ValidationReport) -> None:
        """Final gate over the rendered document."""
        hits = self.matches(text)
        if hits:
            report.error(
                f"[tolerance] final document still contains excluded terms "
                f"{sorted(set(h.lower() for h in hits))} at stage {stage}."
            )
