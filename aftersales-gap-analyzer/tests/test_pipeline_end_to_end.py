"""End-to-end run on fixtures: the guards must be observable in the output."""

import pytest

from afsgap.config import Settings
from afsgap.llm.offline import OfflineClient, seed_page_cache
from afsgap.pipeline import Pipeline

PROCESS = "data/processes/defective_parts_return.yaml"


@pytest.fixture
def run(tmp_path, fixtures_dir):
    settings = Settings(cache_dir=tmp_path / "cache", output_dir=tmp_path / "out")
    settings.ensure_dirs()
    seed_page_cache(fixtures_dir, settings.cache_dir)
    pipeline = Pipeline(settings, OfflineClient(fixtures_dir), offline=True, use_cache=False)
    result, paths = pipeline.run(PROCESS)
    return result, paths, paths["markdown"].read_text(encoding="utf-8")


def test_run_completes_and_passes_validation(run):
    result, _, _ = run
    assert result.validation.passed, result.validation.errors
    assert result.gap_analysis.verdict in {
        "best_in_class", "at_par", "improvement_needed", "insufficient_evidence"
    }
    assert result.gap_analysis.gaps


def test_tolerance_never_reaches_the_document(run):
    _, _, document = run
    body = document.split("## 9. Exclusion")[0]
    for term in ["tolerance", "over-delivery", "overdelivery", "under-delivery"]:
        assert term not in body.lower().replace("tolerance topics are excluded", ""), term


def test_tolerance_is_filtered_at_input_research_and_output(run):
    result, _, _ = run
    stages = {record.stage for record in result.validation.exclusions if record.rule == "tolerance"}
    assert "current_process.input" in stages, "pre-LLM input filtering did not run"
    assert "sap_research.evidence" in stages, "pre-LLM research filtering did not run"
    assert "sap_research.output" in stages, "post-LLM validation did not run"


def test_only_verified_tcodes_are_published(run):
    result, _, document = run
    published = {code.tcode for code in result.sap_research.verified_tcodes}
    assert published == {"VA01", "VL01N"}
    rejected = {code.tcode for code in result.sap_research.dropped_tcodes}
    assert rejected == {"ZRET9", "MIGO"}
    body = document.split("**Rejected transaction codes**")[0]
    assert "ZRET9" not in body


def test_every_verified_tcode_cites_an_sap_domain(run):
    result, _, _ = run
    for code in result.sap_research.verified_tcodes:
        assert "sap.com" in code.source_url


def test_industry_section_has_no_metrics(run):
    _, _, document = run
    section = document.split("## 4. Industry practice benchmark")[1].split("## 5.")[0]
    for token in ["%", "within 5 days", "fill rate", "OTIF"]:
        assert token not in section, f"metric leaked into the industry section: {token}"


def test_non_credible_sources_are_rejected_not_used(run):
    result, _, _ = run
    kept = {source.domain for source in result.industry_research.sources}
    rejected = {source.domain for source in result.industry_research.rejected_sources}
    assert "reddit.com" in rejected and "reddit.com" not in kept
    assert "mckinsey.com" in kept


def test_reports_are_written(run):
    _, paths, document = run
    assert paths["markdown"].exists() and paths["json"].exists()
    for heading in [
        "## 1. Verdict",
        "## 3. SAP standard reference",
        "## 4. Industry practice benchmark",
        "## 5. Gap analysis",
        "## 6. TO-BE process design",
        "## 8. Evidence register",
        "## 9. Exclusion and validation log",
    ]:
        assert heading in document


def test_stage_cache_avoids_a_second_research_call(tmp_path, fixtures_dir):
    settings = Settings(cache_dir=tmp_path / "cache", output_dir=tmp_path / "out")
    settings.ensure_dirs()
    seed_page_cache(fixtures_dir, settings.cache_dir)
    client = OfflineClient(fixtures_dir)
    Pipeline(settings, client, offline=True, use_cache=True).run(PROCESS)
    first = client._research_calls
    Pipeline(settings, client, offline=True, use_cache=True).run(PROCESS)
    assert client._research_calls == first, "cached stages should not re-run research"
