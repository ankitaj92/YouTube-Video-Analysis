"""Scoreboards and comparisons.

The comparison is the point: a single score tells you nothing, but the same
score before and after a change tells you whether the change helped.
"""

from __future__ import annotations

import json
from pathlib import Path

from .schema import EvalRun


def _bar(value: float, width: int = 10) -> str:
    filled = round(value * width)
    return "#" * filled + "." * (width - filled)


def scoreboard(run: EvalRun) -> str:
    lines: list[str] = []
    add = lines.append
    add(f"Eval run: {run.label}   ({run.started_at:%Y-%m-%d %H:%M})")
    add(f"  backend {run.backend}   model {run.model or 'n/a'}   grader {run.judge}")
    add("")
    add(f"{'case':<28} {'compliance':>12} {'grounding':>11} {'insight':>10}  {'blocking':>8}  time")
    add("-" * 88)

    for case in run.cases:
        if not case.ran:
            add(f"{case.case_id:<28} {'DID NOT RUN':>12}  {case.error[:40]}")
            continue
        blocking = case.score.blocking_failures
        add(
            f"{case.case_id:<28} "
            f"{case.score.compliance:>11.0%} {case.score.grounding:>10.0%} "
            f"{case.score.insight:>9.0%}  {blocking:>8}  {case.duration_seconds:>5.0f}s"
        )

    total = run.aggregate()
    add("-" * 88)
    add(f"{'OVERALL':<28} {total.compliance:>11.0%} {total.grounding:>10.0%} {total.insight:>9.0%}"
        f"  {total.blocking_failures:>8}")
    add("")
    add(f"  compliance {_bar(total.compliance)}   must be 100% - anything less is a defect")
    add(f"  grounding  {_bar(total.grounding)}   are findings tied to retrieved evidence")
    add(f"  insight    {_bar(total.insight)}   does it find what the business already knows")

    failures = [(case.case_id, check) for case in run.cases for check in case.failures()]
    if failures:
        add("")
        add(f"Failures ({len(failures)}):")
        for case_id, check in failures[:25]:
            marker = "BLOCKING" if check.must else "        "
            add(f"  {marker} {case_id:<24} {check.id:<28} {check.detail[:60]}")
        if len(failures) > 25:
            add(f"  ... and {len(failures) - 25} more")
    return "\n".join(lines)


def compare(before: EvalRun, after: EvalRun) -> str:
    """Did the change help? Per case, per family."""
    lines: list[str] = []
    add = lines.append
    add(f"Comparing: {before.label} -> {after.label}")
    add("")
    add(f"{'case':<28} {'compliance':>18} {'grounding':>18} {'insight':>18}")
    add("-" * 88)

    before_cases = {case.case_id: case for case in before.cases}
    for case in after.cases:
        old = before_cases.get(case.case_id)
        if old is None:
            add(f"{case.case_id:<28} {'(new case)':>18}")
            continue
        add(
            f"{case.case_id:<28}"
            f"{_delta(old.score.compliance, case.score.compliance):>18}"
            f"{_delta(old.score.grounding, case.score.grounding):>18}"
            f"{_delta(old.score.insight, case.score.insight):>18}"
        )

    old_total, new_total = before.aggregate(), after.aggregate()
    add("-" * 88)
    add(
        f"{'OVERALL':<28}"
        f"{_delta(old_total.compliance, new_total.compliance):>18}"
        f"{_delta(old_total.grounding, new_total.grounding):>18}"
        f"{_delta(old_total.insight, new_total.insight):>18}"
    )
    add("")
    if new_total.blocking_failures > old_total.blocking_failures:
        add(f"  WARNING: blocking failures rose from {old_total.blocking_failures} "
            f"to {new_total.blocking_failures}. Do not ship this change.")
    elif new_total.blocking_failures < old_total.blocking_failures:
        add(f"  Blocking failures fell from {old_total.blocking_failures} "
            f"to {new_total.blocking_failures}.")
    return "\n".join(lines)


def _delta(old: float, new: float) -> str:
    change = new - old
    arrow = "=" if abs(change) < 0.005 else ("up" if change > 0 else "DOWN")
    return f"{old:.0%} -> {new:.0%} {arrow}"


def markdown_report(run: EvalRun) -> str:
    total = run.aggregate()
    lines = [
        f"# Eval report - {run.label}",
        "",
        f"**Run:** {run.started_at:%Y-%m-%d %H:%M} | **Backend:** {run.backend} | "
        f"**Model:** {run.model or 'n/a'} | **Grader:** {run.judge}",
        "",
        "| Family | Score | Meaning |",
        "| --- | --- | --- |",
        f"| Compliance | {total.compliance:.0%} | Rules the output must obey. Below 100% is a defect. |",
        f"| Grounding | {total.grounding:.0%} | Findings tied to retrieved, allowlisted evidence. |",
        f"| Insight | {total.insight:.0%} | Addresses the problems the business already documented. |",
        "",
        f"**Blocking failures:** {total.blocking_failures}",
        "",
        "## Per case",
        "",
        "| Case | Mode | Compliance | Grounding | Insight | Blocking |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for case in run.cases:
        if not case.ran:
            lines.append(f"| {case.case_id} | - | - | - | - | did not run: {case.error[:50]} |")
            continue
        lines.append(
            f"| {case.case_id} | {case.mode} | {case.score.compliance:.0%} | "
            f"{case.score.grounding:.0%} | {case.score.insight:.0%} | {case.score.blocking_failures} |"
        )

    failures = [(case.case_id, check) for case in run.cases for check in case.failures()]
    if failures:
        lines += ["", "## Failures", "", "| Case | Check | Blocking | Detail |", "| --- | --- | --- | --- |"]
        for case_id, check in failures:
            lines.append(f"| {case_id} | {check.id} | {'yes' if check.must else 'no'} | "
                         f"{check.detail[:90].replace('|', '/')} |")
    return "\n".join(lines) + "\n"


def load_run(path: Path) -> EvalRun:
    return EvalRun.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))
