"""Load and pre-filter the AS-IS process description."""

from __future__ import annotations

from pathlib import Path

import yaml

from ..filters.tolerance import ToleranceFilter
from ..models import CurrentProcess, ValidationReport


def load_current_process(
    path: str | Path,
    tolerance: ToleranceFilter,
    report: ValidationReport,
) -> CurrentProcess:
    """Read the process definition and strip excluded topics before anything else.

    This is the pre-LLM half of the tolerance exclusion: whatever the analyst
    wrote about tolerances never enters a prompt, a research query or a report.
    """
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}

    # normalise step sequence numbers if the author left them out
    for index, step in enumerate(raw.get("steps", []) or [], start=1):
        step.setdefault("seq", index)

    cleaned = tolerance.scrub_input(raw, "current_process.input", report)
    process = CurrentProcess.model_validate(cleaned)
    if not process.steps:
        report.warn("[current] the process definition contains no steps; gap analysis will be shallow.")
    return process
