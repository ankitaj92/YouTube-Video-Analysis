"""The exists / does-not-exist decision, and the greenfield branch it triggers."""

import re

import pytest

from afsgap.config import Settings
from afsgap.llm.offline import OfflineClient, seed_page_cache
from afsgap.models import ValidationReport
from afsgap.pipeline import Pipeline
from afsgap.sources.confluence import ConfluenceUnavailableError
from afsgap.sources.offline import OfflineConfluenceClient
from afsgap.sources.resolver import ProcessResolver, slugify


@pytest.fixture
def resolver(fixtures_dir, tolerance):
    settings = Settings()
    return ProcessResolver(
        settings, OfflineClient(fixtures_dir), tolerance, OfflineConfluenceClient(fixtures_dir)
    )


# -- resolution -------------------------------------------------------------
def test_known_process_resolves_to_gap_mode(resolver, report):
    resolved = resolver.resolve("Defective Parts Return", report)
    assert resolved.mode == "gap"
    assert resolved.provenance.source == "confluence"
    assert resolved.provenance.page_id == "123456"
    assert resolved.provenance.match_score == 1.0
    assert resolved.process is not None and resolved.process.steps


def test_unknown_process_falls_back_to_greenfield(resolver, report):
    resolved = resolver.resolve("Battery Pack Return", report)
    assert resolved.mode == "greenfield"
    assert resolved.process is None
    assert resolved.provenance.source == "none"
    assert "above the match threshold" in resolved.provenance.not_found_reason
    assert resolved.provenance.candidates, "rejected candidates must be recorded for review"
    assert any("[source]" in warning for warning in report.warnings)


def test_require_existing_refuses_to_invent(resolver, report):
    with pytest.raises(LookupError, match="No Confluence page matched"):
        resolver.resolve("Battery Pack Return", report, require_existing=True)


def test_force_new_skips_the_lookup(resolver, report):
    resolved = resolver.resolve("Defective Parts Return", report, force_new=True)
    assert resolved.mode == "greenfield"
    assert "not searched" in resolved.provenance.not_found_reason


def test_explicit_page_id_overrides_search(resolver, report):
    resolved = resolver.resolve("Anything At All", report, page_id="123456")
    assert resolved.mode == "gap"
    assert resolved.provenance.page_id == "123456"


def test_yaml_path_still_works(resolver, report):
    resolved = resolver.resolve("data/processes/defective_parts_return.yaml", report)
    assert resolved.mode == "gap"
    assert resolved.provenance.source == "yaml"


def test_unconfigured_confluence_degrades_to_greenfield(fixtures_dir, tolerance, report):
    unconfigured = ProcessResolver(Settings(confluence_base_url=""), OfflineClient(fixtures_dir), tolerance)
    resolved = unconfigured.resolve("Defective Parts Return", report)
    assert resolved.mode == "greenfield"
    assert "could not be reached" in resolved.provenance.not_found_reason


def test_unconfigured_confluence_raises_when_existing_required(fixtures_dir, tolerance, report):
    unconfigured = ProcessResolver(Settings(confluence_base_url=""), OfflineClient(fixtures_dir), tolerance)
    with pytest.raises(ConfluenceUnavailableError):
        unconfigured.resolve("Defective Parts Return", report, require_existing=True)


def test_confluence_content_is_tolerance_filtered_before_extraction(resolver, report):
    resolver.resolve("Defective Parts Return", report)
    stages = {record.stage for record in report.exclusions if record.rule == "tolerance"}
    assert "current_process.confluence" in stages, "page text must be scrubbed before it reaches a prompt"


def test_slugify():
    assert slugify("Defective Parts Return") == "defective_parts_return"
    assert slugify("Core / Exchange Return!") == "core___exchange_return"


# -- greenfield run ---------------------------------------------------------
@pytest.fixture
def greenfield(tmp_path, fixtures_dir):
    settings = Settings(cache_dir=tmp_path / "cache", output_dir=tmp_path / "out")
    settings.ensure_dirs()
    seed_page_cache(fixtures_dir, settings.cache_dir)
    pipeline = Pipeline(settings, OfflineClient(fixtures_dir), offline=True, use_cache=False)
    result, paths = pipeline.run(
        "Battery Pack Return", confluence=OfflineConfluenceClient(fixtures_dir)
    )
    return result, paths, paths["markdown"].read_text(encoding="utf-8")


def test_greenfield_produces_a_blueprint_not_a_gap_analysis(greenfield):
    result, _, _ = greenfield
    assert result.mode == "greenfield"
    assert result.blueprint is not None
    assert result.gap_analysis is None
    assert result.current_process is None
    assert result.validation.passed, result.validation.errors


def test_blueprint_document_states_why_it_is_new(greenfield):
    _, _, document = greenfield
    assert "NEW PROCESS" in document
    assert "## 2. Process source" in document
    assert "Battery Pack Return" in document


def test_blueprint_still_carries_sap_and_industry_evidence(greenfield):
    _, _, document = greenfield
    assert "## 3. SAP standard reference" in document
    assert "## 4. Industry practice benchmark" in document
    assert "## 9. Evidence register" in document


def test_blueprint_decisions_admit_when_sap_is_silent(greenfield):
    result, _, document = greenfield
    assert any(not decision.sap_basis for decision in result.blueprint.design_decisions)
    assert "_not established from the retrieved SAP sources_" in document


def test_blueprint_is_written_under_its_own_name(greenfield):
    _, paths, _ = greenfield
    assert paths["markdown"].name.endswith("_blueprint.md")


def test_blueprint_obeys_the_same_exclusions(greenfield):
    _, _, document = greenfield
    body = re.sub(r"<!-- afsgap:meta -->.*?<!-- /afsgap:meta -->", " ", document, flags=re.DOTALL)
    body = body.split("Exclusion and validation log")[0]
    assert "tolerance" not in body.lower()
    industry = re.search(
        r"^## \d+\. Industry practice benchmark(.*?)(?=^## \d+\.)", body, re.MULTILINE | re.DOTALL
    )
    for token in ["%", "within 5 days", "fill rate"]:
        assert token not in industry.group(1)


def test_greenfield_only_cites_verified_tcodes(greenfield):
    result, _, _ = greenfield
    assert not any("leaked" in error for error in result.validation.errors)
