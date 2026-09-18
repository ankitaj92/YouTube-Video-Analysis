"""The final gate is the last line of defence over the rendered document."""

import pytest

from afsgap.models import (
    CurrentProcess,
    DroppedTCode,
    GapAnalysis,
    IndustryBenchmark,
    IndustryResearchResult,
    RunResult,
    SapResearchResult,
    SapStandardProcess,
    ValidationReport,
    VerifiedTCode,
)
from afsgap.validation import validate_document
from datetime import date


def _result(verified=(), dropped=()):
    return RunResult(
        process_id="p",
        process_name="P",
        run_date=date.today(),
        current_process=CurrentProcess(process_id="p", process_name="P"),
        sap_research=SapResearchResult(
            standard=SapStandardProcess(process_name="P"),
            verified_tcodes=[VerifiedTCode(tcode=t, source_url="https://help.sap.com/x") for t in verified],
            dropped_tcodes=[DroppedTCode(tcode=t, reason="unverified") for t in dropped],
        ),
        industry_research=IndustryResearchResult(benchmark=IndustryBenchmark(process_name="P")),
        gap_analysis=GapAnalysis(process_name="P", executive_summary="", verdict="at_par", verdict_rationale=""),
        validation=ValidationReport(),
    )


def test_tolerance_in_the_body_fails_the_run(tolerance, kpi, report):
    validate_document("The tolerance key is maintained per plant.", _result(), tolerance, kpi, report)
    assert not report.passed


def test_meta_block_is_exempt(tolerance, kpi, report):
    document = "<!-- afsgap:meta -->\nTolerance topics are excluded.\n<!-- /afsgap:meta -->\nThe dealer returns the part."
    validate_document(document, _result(), tolerance, kpi, report)
    assert report.passed


def test_exclusion_log_is_not_reflagged(tolerance, kpi, report):
    document = "Clean body.\n\n## 9. Exclusion and validation log\n\n| tolerance | input | x | the delivery tolerance is checked | tolerance |"
    validate_document(document, _result(), tolerance, kpi, report)
    assert report.passed


def test_kpi_in_the_industry_section_fails(tolerance, kpi, report):
    document = (
        "## 4. Industry practice benchmark\n\nLeaders settle claims within 5 days.\n\n"
        "## 5. Gap analysis\n\nnothing here\n"
    )
    validate_document(document, _result(), tolerance, kpi, report)
    assert not report.passed
    assert any("[kpi]" in error for error in report.errors)


def test_kpi_outside_the_industry_section_is_allowed(tolerance, kpi, report):
    document = "## 2. Current process\n\nThe backlog is 40 days today.\n\n## 5. Gap analysis\n"
    validate_document(document, _result(), tolerance, kpi, report)
    assert report.passed


def test_rejected_tcode_leaking_into_the_body_is_an_error(tolerance, kpi, report):
    validate_document("Use transaction MIGO to post the receipt.", _result(dropped=["MIGO"]), tolerance, kpi, report)
    assert not report.passed
    assert any("leaked" in error for error in report.errors)


def test_verified_tcode_passes(tolerance, kpi, report):
    validate_document("Use transaction VA01 to create the order.", _result(verified=["VA01"]), tolerance, kpi, report)
    assert report.passed


def test_unverified_tcode_is_warned(tolerance, kpi, report):
    validate_document("Use transaction VL10B to release.", _result(), tolerance, kpi, report)
    assert report.passed  # warning, not a hard failure
    assert any("never verified" in warning for warning in report.warnings)
