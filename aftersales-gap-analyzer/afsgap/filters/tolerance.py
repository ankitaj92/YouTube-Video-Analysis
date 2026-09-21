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

import re
from typing import Any

from ..models import ExclusionRecord, ValidationReport
from ..resources_loader import compile_contextual, compile_patterns, load_resource
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


class ExcludedProcessError(RuntimeError):
    """Raised only in strict mode, when the requested process is an excluded topic."""


class ToleranceFilter(PatternFilter):
    rule_name = "tolerance"

    def __init__(self, resource_dir: str | None = None) -> None:
        data = load_resource("tolerance_terms", resource_dir)
        super().__init__(
            patterns=compile_patterns(data.get("patterns", [])),
            allowances=compile_patterns(data.get("allowances", [])),
            contextual=compile_contextual(data.get("contextual", [])),
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

    def check_process_name(self, process_name: str) -> list[str]:
        """Report whether the requested process name overlaps the exclusion list.

        This is advisory, not a veto. Users search for whatever their landscape
        calls the process, and a name is an identifier, not a claim: "Underdelivery"
        is a real aftersales process (a short shipment raised as a discrepancy),
        even though tolerance configuration also talks about under-delivery.

        The caller decides what to do. By default the run proceeds, the name is
        allowed through the filters so the document can be titled, and the
        overlap is recorded. A programme that wants the stricter behaviour can
        turn refusal on.
        """
        return sorted({hit.lower() for hit in self.matches(process_name)})

    def allow_process_name(self, process_name: str) -> None:
        """Let the process name itself survive filtering for this run.

        Without this, a process called "Tolerance Management" would have its own
        title stripped out of the document. The allowance covers the name as a
        phrase only - tolerance *content* is still excluded exactly as before.
        """
        name = (process_name or "").strip()
        if not name:
            return
        self.allowances.append(re.compile(re.escape(name), re.IGNORECASE))

    def assert_clean(self, text: str, stage: str, report: ValidationReport) -> None:
        """Final gate over the rendered document.

        Scanned sentence by sentence, because a contextual rule asks whether
        *this* statement is about tolerances. Judging the whole document at once
        would let the word "limit" in a roadmap make an unrelated sentence about
        an under-delivery look like tolerance configuration.
        """
        from .segmentation import segment

        hits: list[str] = []
        for part in segment(text):
            hits.extend(self.matches(part))
        if hits:
            report.error(
                f"[tolerance] final document still contains excluded terms "
                f"{sorted(set(h.lower() for h in hits))} at stage {stage}."
            )
