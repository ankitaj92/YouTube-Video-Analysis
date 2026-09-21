"""Run the eval set and score it.

Two modes, because re-running the pipeline is the expensive part:

* ``execute`` - run each case through the pipeline, then score it.
* ``score`` - score run records that already exist in ``output/``. Free, and
  the mode you use while tuning the grader or adding expectations.

Results are written to ``evals/results/`` so any two runs can be compared.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Iterable

import yaml

from ..analysis.current_process import load_current_process
from ..config import Settings
from ..filters.tolerance import ToleranceFilter
from ..models import RunResult, ValidationReport
from ..pipeline import Pipeline
from ..report.design_doc import render_markdown
from .checks import run_checks
from .judge import Grader, KeywordGrader
from .rubric import derive_rubric
from .schema import CaseResult, CaseScore, CheckResult, EvalCase, EvalRun

logger = logging.getLogger(__name__)

CASES_DIR = Path(__file__).resolve().parents[2] / "evals" / "cases"
RESULTS_DIR = Path(__file__).resolve().parents[2] / "evals" / "results"


def load_cases(directory: Path | None = None, only: Iterable[str] | None = None) -> list[EvalCase]:
    directory = Path(directory or CASES_DIR)
    wanted = {name.lower() for name in (only or [])}
    cases: list[EvalCase] = []
    for path in sorted(directory.glob("*.yaml")):
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        case = EvalCase.model_validate(payload)
        if not case.enabled:
            continue
        if wanted and case.id.lower() not in wanted and not (wanted & set(t.lower() for t in case.tags)):
            continue
        cases.append(case)
    return cases


def analysis_text(result: RunResult) -> str:
    """The part of the document that represents the tool's own conclusions.

    The rubric asks what the analysis concluded, so the retrieved research is
    deliberately excluded - otherwise an expectation could be satisfied by a
    source that happened to mention the words.
    """
    parts: list[str] = []
    if result.gap_analysis:
        analysis = result.gap_analysis
        parts += [analysis.executive_summary, analysis.verdict_rationale]
        for gap in analysis.gaps:
            parts += [gap.title, gap.current_state, gap.gap_description, gap.recommendation,
                      gap.sap_standard_reference, gap.industry_reference, gap.s4_disposition,
                      " ".join(gap.dependencies)]
        for step in analysis.to_be_process:
            parts += [step.name, step.description, step.change_vs_current]
        parts += analysis.quick_wins + analysis.roadmap_phases + analysis.risks + analysis.assumptions
    if result.blueprint:
        blueprint = result.blueprint
        parts += [blueprint.summary, blueprint.why_new] + blueprint.design_principles
        for decision in blueprint.design_decisions:
            parts += [decision.decision, decision.recommendation, decision.rationale,
                      decision.sap_basis, decision.industry_basis] + decision.options_considered
        for step in blueprint.recommended_flow:
            parts += [step.name, step.description, step.change_vs_current]
        parts += (blueprint.configuration_scope + blueprint.master_data_prerequisites
                  + blueprint.integration_points + blueprint.risks + blueprint.roadmap_phases
                  + blueprint.open_questions + blueprint.assumptions)
    return "\n".join(part for part in parts if part)


def score_case(case: EvalCase, result: RunResult, document: str, grader: Grader) -> CaseResult:
    checks: list[CheckResult] = list(run_checks(case, result, document))

    rubric = derive_rubric(result.current_process) + list(case.rubric)
    if rubric:
        checks.extend(grader.grade(rubric, analysis_text(result)))

    outcome = CaseResult(
        case_id=case.id, mode=result.mode, checks=checks,
        judged=grader.name != "keywords",
    )
    outcome.score = _score(checks)
    return outcome


def _score(checks: list[CheckResult]) -> CaseScore:
    def family_score(family: str) -> float:
        relevant = [c for c in checks if c.family == family and c.outcome != "skipped"]
        if not relevant:
            return 1.0
        points = sum(1.0 if c.outcome == "pass" else 0.5 if c.outcome == "partial" else 0.0
                     for c in relevant)
        return points / len(relevant)

    blocking = sum(1 for c in checks if c.must and c.outcome == "fail")
    return CaseScore(
        compliance=family_score("compliance"),
        grounding=family_score("grounding"),
        insight=family_score("insight"),
        blocking_failures=blocking,
    )


class EvalRunner:
    def __init__(self, settings: Settings, client, grader: Grader | None = None,
                 confluence=None, offline: bool = False) -> None:
        self.settings = settings
        self.client = client
        self.grader = grader or KeywordGrader()
        self.confluence = confluence
        self.offline = offline

    # ------------------------------------------------------------------
    def execute(self, cases: list[EvalCase], label: str = "", use_cache: bool = True) -> EvalRun:
        run = EvalRun(
            label=label or "run",
            backend=type(self.client).__name__,
            model=getattr(self.settings, "ollama_model", "") or self.settings.model,
            judge=self.grader.name,
        )
        for case in cases:
            logger.info("eval: %s", case.id)
            started = time.monotonic()
            try:
                pipeline = Pipeline(self.settings, self.client,
                                    offline=self.offline or case.offline, use_cache=use_cache)
                result, paths = pipeline.run(
                    case.target,
                    skip_industry=case.skip_industry,
                    force_new=case.force_new,
                    confluence=self.confluence,
                )
                document = paths["markdown"].read_text(encoding="utf-8")
                outcome = score_case(case, result, document, self.grader)
                outcome.run_json = str(paths["json"])
                if case.mode != "any" and result.mode != case.mode:
                    outcome.checks.append(CheckResult(
                        id="expected_mode", family="compliance", outcome="fail",
                        detail=f"expected {case.mode}, ran {result.mode}",
                    ))
                    outcome.score = _score(outcome.checks)
            except Exception as exc:
                logger.exception("eval case %s failed", case.id)
                outcome = CaseResult(case_id=case.id, ran=False, error=f"{type(exc).__name__}: {exc}")
            outcome.duration_seconds = round(time.monotonic() - started, 1)
            run.cases.append(outcome)
        return run

    # ------------------------------------------------------------------
    def score_existing(self, cases: list[EvalCase], run_files: list[Path], label: str = "") -> EvalRun:
        """Score run records already on disk - no pipeline, no cost."""
        run = EvalRun(label=label or "rescore", backend="from-disk", judge=self.grader.name)
        by_process = {}
        for path in run_files:
            payload = json.loads(path.read_text(encoding="utf-8"))
            by_process.setdefault(payload.get("process_id", ""), []).append((path, payload))

        for case in cases:
            process_id = _process_id_for(case)
            candidates = by_process.get(process_id, [])
            if not candidates:
                run.cases.append(CaseResult(case_id=case.id, ran=False,
                                            error=f"no run record found for '{process_id}'"))
                continue
            path, payload = sorted(candidates, key=lambda pair: pair[0].stat().st_mtime)[-1]
            result = RunResult.model_validate(payload)
            document = render_markdown(result)
            outcome = score_case(case, result, document, self.grader)
            outcome.run_json = str(path)
            run.cases.append(outcome)
        return run


def _process_id_for(case: EvalCase) -> str:
    from ..sources.resolver import slugify

    target = case.target
    if target.endswith((".yaml", ".yml")):
        path = Path(target)
        if path.exists():
            process = load_current_process(path, ToleranceFilter(), ValidationReport())
            return process.process_id
        return path.stem
    return slugify(target)


def save(run: EvalRun, directory: Path | None = None) -> Path:
    directory = Path(directory or RESULTS_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = run.started_at.strftime("%Y%m%d-%H%M%S")
    path = directory / f"{stamp}_{(run.label or 'run').replace(' ', '-')}.json"
    path.write_text(json.dumps(run.model_dump(mode="json"), indent=2, ensure_ascii=False), encoding="utf-8")
    return path
