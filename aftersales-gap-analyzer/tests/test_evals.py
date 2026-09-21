"""The eval harness itself must be trustworthy - it is what judges everything else."""

import json
from datetime import date

import pytest

from afsgap.config import Settings
from afsgap.evals.checks import body_of, run_checks
from afsgap.evals.judge import KeywordGrader, ModelGrader
from afsgap.evals.report import compare, markdown_report, scoreboard
from afsgap.evals.rubric import derive_rubric, keywords_from
from afsgap.evals.runner import analysis_text, load_cases, score_case, _score
from afsgap.evals.schema import CaseResult, CaseScore, CheckResult, EvalCase, EvalRun, RubricItem
from afsgap.llm.offline import OfflineClient, seed_page_cache
from afsgap.models import (
    CurrentProcess,
    Gap,
    GapAnalysis,
    ProcessStep,
)
from afsgap.pipeline import Pipeline
from afsgap.report.design_doc import render_markdown
from afsgap.sources.offline import OfflineConfluenceClient


# -- the eval set on disk ---------------------------------------------------
def test_the_eval_set_loads():
    cases = load_cases()
    assert len(cases) >= 10, "an eval set smaller than ten cases measures very little"
    assert len({case.id for case in cases}) == len(cases), "case ids must be unique"


def test_the_set_covers_both_modes_and_the_guards():
    cases = load_cases()
    tags = {tag for case in cases for tag in case.tags}
    modes = {case.mode for case in cases}
    assert "gap" in modes and "greenfield" in modes
    assert "guard" in tags, "the deterministic guard cases are the ones that run for free"
    assert any(case.offline for case in cases)


def test_every_case_targets_something_that_exists(tmp_path):
    from pathlib import Path

    for case in load_cases():
        if case.target.endswith((".yaml", ".yml")):
            assert Path(case.target).exists(), f"{case.id} points at a missing process file"


# -- expectations derived from the process definition -----------------------
def _process() -> CurrentProcess:
    return CurrentProcess(
        process_id="p", process_name="P",
        known_pain_points=["Inspection evidence is not auditable", "Dealers cannot see status"],
        custom_objects=["ZRET_AUTH - custom table", "ZRET_RPT - custom report"],
        steps=[ProcessStep(seq=1, name="Receive", pain_points=["Cartons pile up in quarantine"])],
    )


def test_pain_points_become_expectations():
    items = derive_rubric(_process())
    derived = [item for item in items if item.source == "derived_pain_point"]
    assert len(derived) == 3, "two summary pain points and one step-level pain point"
    assert all(item.keywords for item in derived)


def test_summary_pain_points_are_blocking_and_step_ones_are_not():
    items = {item.id: item for item in derive_rubric(_process())}
    assert items["pain01"].must is True
    assert items["step01pain1"].must is False


def test_custom_objects_must_be_dispositioned():
    items = [item for item in derive_rubric(_process()) if item.source == "derived_custom_object"]
    assert len(items) == 2
    assert all(item.must for item in items)
    assert items[0].keywords == ["zret_auth"]


def test_no_process_means_no_derived_expectations():
    assert derive_rubric(None) == []


def test_keyword_extraction_drops_noise():
    words = keywords_from("The inspection evidence is not auditable in the system")
    assert "inspection" in words and "auditable" in words
    assert "the" not in words and "not" not in words


# -- the grader -------------------------------------------------------------
def test_keyword_grader_credits_a_covered_expectation():
    item = RubricItem(id="x", description="d", keywords=["inspection", "structured", "codes"])
    result = KeywordGrader().grade([item], "Inspection outcomes become structured disposition codes.")[0]
    assert result.outcome == "pass"


def test_keyword_grader_fails_an_ignored_expectation():
    item = RubricItem(id="x", description="d", keywords=["quarantine", "cartons"])
    result = KeywordGrader().grade([item], "The analysis talks about something else entirely.")[0]
    assert result.outcome == "fail"


class _StubJudge:
    """A judge that claims everything is addressed - including with a fake quote."""

    def __init__(self, quote: str):
        self.quote = quote

    def extract(self, *, system, prompt, schema):
        return schema.model_validate(
            {"verdicts": [{"id": "x", "addressed": True, "quote": self.quote, "reason": ""}]}
        )


def test_model_grader_accepts_a_verdict_backed_by_a_real_quote():
    document = "The inspection moves into the system of record with structured codes."
    grader = ModelGrader(_StubJudge("The inspection moves into the system of record"))
    result = grader.grade([RubricItem(id="x", description="d")], document)[0]
    assert result.outcome == "pass"


def test_model_grader_downgrades_a_verdict_whose_quote_is_invented():
    """A judge that cannot quote the document is guessing, and is scored as such."""
    grader = ModelGrader(_StubJudge("This sentence does not appear anywhere in the analysis."))
    result = grader.grade([RubricItem(id="x", description="d")], "Some unrelated analysis text.")[0]
    assert result.outcome == "partial"
    assert "not in the document" in result.detail


def test_judge_failure_falls_back_to_keywords():
    class Broken:
        def extract(self, **kwargs):
            raise RuntimeError("model unavailable")

    grader = ModelGrader(Broken())
    item = RubricItem(id="x", description="d", keywords=["inspection"])
    result = grader.grade([item], "inspection is addressed")[0]
    assert result.outcome == "pass", "a broken judge must not fail the whole eval"


# -- scoring ----------------------------------------------------------------
def test_families_are_scored_separately():
    checks = [
        CheckResult(id="a", family="compliance", outcome="fail"),
        CheckResult(id="b", family="grounding", outcome="pass"),
        CheckResult(id="c", family="insight", outcome="pass"),
    ]
    score = _score(checks)
    assert score.compliance == 0.0 and score.grounding == 1.0 and score.insight == 1.0


def test_partial_counts_as_half():
    assert _score([CheckResult(id="a", family="insight", outcome="partial")]).insight == 0.5


def test_skipped_checks_do_not_drag_the_score_down():
    checks = [
        CheckResult(id="a", family="grounding", outcome="pass"),
        CheckResult(id="b", family="grounding", outcome="skipped"),
    ]
    assert _score(checks).grounding == 1.0


def test_blocking_failures_are_counted_not_averaged():
    checks = [
        CheckResult(id="a", family="insight", outcome="fail", must=True),
        CheckResult(id="b", family="insight", outcome="fail", must=False),
    ]
    assert _score(checks).blocking_failures == 1


# -- analysis text ----------------------------------------------------------
def test_only_the_tools_own_conclusions_are_graded():
    """An expectation must not be satisfied by a source that mentioned the words."""
    from afsgap.models import IndustryBenchmark, IndustryResearchResult, RunResult, SapResearchResult, SapStandardProcess, ValidationReport

    result = RunResult(
        process_id="p", process_name="P", run_date=date.today(),
        sap_research=SapResearchResult(
            standard=SapStandardProcess(process_name="P", summary="RESEARCH_ONLY_MARKER")
        ),
        industry_research=IndustryResearchResult(benchmark=IndustryBenchmark(process_name="P")),
        gap_analysis=GapAnalysis(
            process_name="P", executive_summary="ANALYSIS_MARKER", verdict="at_par",
            verdict_rationale="because", gaps=[],
        ),
        validation=ValidationReport(),
    )
    text = analysis_text(result)
    assert "ANALYSIS_MARKER" in text
    assert "RESEARCH_ONLY_MARKER" not in text


# -- checks against a real run ---------------------------------------------
@pytest.fixture
def scored(tmp_path, fixtures_dir):
    settings = Settings(cache_dir=tmp_path / "c", output_dir=tmp_path / "o")
    settings.ensure_dirs()
    seed_page_cache(fixtures_dir, settings.cache_dir)
    pipeline = Pipeline(settings, OfflineClient(fixtures_dir), offline=True, use_cache=False)
    result, paths = pipeline.run("data/processes/defective_parts_return.yaml",
                                 confluence=OfflineConfluenceClient(fixtures_dir))
    document = paths["markdown"].read_text(encoding="utf-8")
    case = EvalCase(id="c", target="data/processes/defective_parts_return.yaml",
                    forbidden=["delivery tolerance"])
    return case, result, document


def test_a_clean_run_passes_compliance(scored):
    case, result, document = scored
    checks = run_checks(case, result, document)
    failures = [c for c in checks if c.family == "compliance" and c.outcome == "fail"]
    assert not failures, [f"{c.id}: {c.detail}" for c in failures]


def test_the_rejected_tcode_table_is_not_mistaken_for_a_citation(scored):
    """The document publishes what was rejected; that is transparency, not a breach."""
    _, _, document = scored
    assert "ZRET9" in document, "the rejected code should be published"
    assert "ZRET9" not in body_of(document), "but not counted as a claim"


def test_a_tolerance_breach_would_be_caught(scored):
    case, result, document = scored
    broken = document.replace("## 1. Verdict", "## 1. Verdict\n\nThe delivery tolerance is checked.\n")
    checks = {c.id: c for c in run_checks(case, result, broken)}
    assert checks["no_tolerance_content"].outcome == "fail"
    assert checks["no_forbidden_statements"].outcome == "fail"


def test_a_kpi_in_the_industry_section_would_be_caught(scored):
    case, result, document = scored
    broken = document.replace(
        "### 5.1 Leading practices", "### 5.1 Leading practices\n\nClaims settle within 5 days.\n"
    )
    checks = {c.id: c for c in run_checks(case, result, broken)}
    assert checks["industry_is_kpi_free"].outcome == "fail"


def test_an_unverified_tcode_in_the_body_would_be_caught(scored):
    case, result, document = scored
    broken = document.replace("## 1. Verdict", "## 1. Verdict\n\nUse transaction VL10B to release.\n")
    checks = {c.id: c for c in run_checks(case, result, broken)}
    assert checks["only_verified_tcodes"].outcome == "fail"


def test_scoring_a_case_produces_all_three_families(scored):
    case, result, document = scored
    outcome = score_case(case, result, document, KeywordGrader())
    assert outcome.score.compliance == 1.0
    assert outcome.checks
    assert {c.family for c in outcome.checks} == {"compliance", "grounding", "insight"}


# -- reporting --------------------------------------------------------------
def _run(label: str, compliance: float, insight: float) -> EvalRun:
    return EvalRun(
        label=label,
        cases=[CaseResult(case_id="c1", score=CaseScore(compliance=compliance, grounding=1.0,
                                                        insight=insight))],
    )


def test_scoreboard_renders():
    text = scoreboard(_run("x", 1.0, 0.8))
    assert "OVERALL" in text and "compliance" in text


def test_comparison_shows_direction():
    text = compare(_run("before", 1.0, 0.5), _run("after", 1.0, 0.9))
    assert "up" in text


def test_comparison_warns_when_blocking_failures_rise():
    before = _run("before", 1.0, 0.9)
    after = _run("after", 1.0, 0.9)
    after.cases[0].score.blocking_failures = 3
    assert "Do not ship" in compare(before, after)


def test_markdown_report_renders():
    assert "| Family | Score |" in markdown_report(_run("x", 1.0, 0.9))
