"""Regressions for the failures seen on a real laptop run."""

import pytest
import requests

from afsgap.config import Settings
from afsgap.filters.tolerance import ExcludedProcessError, ToleranceFilter
from afsgap.llm.offline import OfflineClient
from afsgap.models import ValidationReport
from afsgap.pipeline import Pipeline
from afsgap.research.transcript import Citation, ResearchTranscript, SearchHit
from afsgap.sources.confluence import ConfluenceClient, ConfluenceUnavailableError, normalise_base_url
from afsgap.sources.offline import OfflineConfluenceClient


# -- Confluence Cloud needs /wiki ------------------------------------------
@pytest.mark.parametrize(
    "given,expected",
    [
        ("https://acme-helios.atlassian.net", "https://acme-helios.atlassian.net/wiki"),
        ("https://acme.atlassian.net/", "https://acme.atlassian.net/wiki"),
        ("https://acme.atlassian.net/wiki", "https://acme.atlassian.net/wiki"),
        ("https://acme.atlassian.net/wiki/", "https://acme.atlassian.net/wiki"),
        # Data Center serves the API at the root - must not be touched
        ("https://confluence.corp.example.com", "https://confluence.corp.example.com"),
        ("https://wiki.corp.example.com/confluence", "https://wiki.corp.example.com/confluence"),
        ("", ""),
    ],
)
def test_cloud_base_url_is_corrected(given, expected):
    assert normalise_base_url(given) == expected


def test_client_uses_the_corrected_url():
    client = ConfluenceClient(Settings(
        confluence_base_url="https://acme.atlassian.net",
        confluence_email="me@acme.com",
        confluence_api_token="t",
    ))
    assert client.base_url.endswith("/wiki")


def test_404_names_the_likely_cause(monkeypatch):
    client = ConfluenceClient(Settings(
        confluence_base_url="https://confluence.corp.example.com",
        confluence_api_token="t",
        confluence_auth="bearer",
    ))

    class Response:
        status_code = 404

    monkeypatch.setattr(client.session, "get", lambda *a, **k: Response())
    with pytest.raises(ConfluenceUnavailableError, match="/wiki"):
        client._get("/rest/api/content/search", {})


# -- users can ask for any process ------------------------------------------
@pytest.mark.parametrize(
    "name", ["Underdelivery", "Over Delivery", "Defective Parts Return", "Short Shipment Handling"]
)
def test_business_process_names_do_not_overlap_the_exclusion(name):
    """These name business events, not tolerance configuration."""
    assert ToleranceFilter().check_process_name(name) == []


def test_a_name_overlapping_the_vocabulary_is_reported_not_refused():
    overlap = ToleranceFilter().check_process_name("Tolerance Management")
    assert overlap, "the overlap should be reported"
    # ...and reporting it is all that happens: no exception


def test_underdelivery_runs_end_to_end(tmp_path, fixtures_dir):
    """The process a user actually asked for must produce a document."""
    settings = Settings(cache_dir=tmp_path / "c", output_dir=tmp_path / "o")
    settings.ensure_dirs()
    pipeline = Pipeline(settings, OfflineClient(fixtures_dir), offline=True, use_cache=False)
    result, paths = pipeline.run("Underdelivery", confluence=OfflineConfluenceClient(fixtures_dir))
    assert result.validation.passed, result.validation.errors
    assert result.blueprint is not None
    document = paths["markdown"].read_text(encoding="utf-8")
    assert "Underdelivery" in document, "the document must be able to name its own process"


def test_strict_mode_refuses_a_genuine_overlap(tmp_path, fixtures_dir):
    """Opt-in behaviour, for a name that really is tolerance configuration."""
    settings = Settings(cache_dir=tmp_path / "c", output_dir=tmp_path / "o",
                        refuse_excluded_process=True)
    settings.ensure_dirs()
    client = OfflineClient(fixtures_dir)
    pipeline = Pipeline(settings, client, offline=True, use_cache=False)
    with pytest.raises(ExcludedProcessError, match="REFUSE_EXCLUDED_PROCESS"):
        pipeline.run("Tolerance Limit Configuration", confluence=OfflineConfluenceClient(fixtures_dir))
    assert client._research_calls == 0, "strict mode should refuse before any research"


def test_strict_mode_does_not_block_a_business_process(tmp_path, fixtures_dir):
    settings = Settings(cache_dir=tmp_path / "c", output_dir=tmp_path / "o",
                        refuse_excluded_process=True)
    settings.ensure_dirs()
    pipeline = Pipeline(settings, OfflineClient(fixtures_dir), offline=True, use_cache=False)
    result, _ = pipeline.run("Underdelivery", confluence=OfflineConfluenceClient(fixtures_dir))
    assert result.blueprint is not None


def test_tolerance_content_is_still_excluded_for_such_a_process(tmp_path, fixtures_dir):
    """Analysing 'Underdelivery' must not smuggle tolerance configuration in."""
    settings = Settings(cache_dir=tmp_path / "c", output_dir=tmp_path / "o")
    settings.ensure_dirs()
    pipeline = Pipeline(settings, OfflineClient(fixtures_dir), offline=True, use_cache=False)
    result, _ = pipeline.run("Underdelivery", confluence=OfflineConfluenceClient(fixtures_dir))
    assert any(record.rule == "tolerance" for record in result.validation.exclusions)


# -- evidence budgeting ------------------------------------------------------
def _transcript(pages: int = 6, page_chars: int = 3000) -> ResearchTranscript:
    transcript = ResearchTranscript(
        text="\n".join(f"### SOURCE {i}\n" + ("body " * (page_chars // 5)) for i in range(pages)),
        hits=[SearchHit(url=f"https://help.sap.com/docs/{i}", title=f"Page {i}", snippet="s" * 200)
              for i in range(40)],
        citations=[Citation(url=f"https://help.sap.com/docs/{i}", cited_text="quote " * 20) for i in range(20)],
    )
    return transcript


def test_unbudgeted_block_is_unchanged():
    block = _transcript().evidence_block()
    assert "## Retrieved sources" in block and "## Research narrative" in block


def test_budget_is_respected():
    block = _transcript().evidence_block(max_chars=6000)
    assert len(block) <= 8000


def test_page_text_survives_the_budget_not_the_url_list():
    """The bug: trimming the assembled string kept the index of the sources and
    threw away the pages the findings are actually made from."""
    transcript = _transcript()
    block = transcript.evidence_block(max_chars=6000)
    narrative = block.split("## Research narrative")[1]
    assert len(narrative) > 3000, "most of the budget must go to retrieved page text"
    listed = block.count("- URL:")
    assert listed < 40, "the source list should be the first thing cut"


def test_narrative_truncation_is_announced():
    block = _transcript().evidence_block(max_chars=4000)
    assert "omitted" in block


# -- search engine fan-out ---------------------------------------------------
def test_ddgs_engines_are_pinned():
    """`auto` fans out over a dozen providers and collects 429s."""
    settings = Settings()
    assert settings.ddgs_backends and settings.ddgs_backends != "auto"


def test_first_token_allowance_exceeds_the_between_token_one():
    """Prompt reading happens before the first token and is much slower."""
    settings = Settings()
    assert settings.ollama_first_token_timeout > settings.ollama_chunk_timeout
