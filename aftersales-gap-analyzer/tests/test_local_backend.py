"""The fully-local path: Ollama for generation, a search backend for retrieval."""

import json

import pytest

from afsgap.config import Settings
from afsgap.llm.ollama import OllamaClient, OllamaUnavailableError
from afsgap.llm.offline import seed_page_cache
from afsgap.llm.schema import flatten_schema
from afsgap.models import GapAnalysis, SapStandardProcess
from afsgap.pipeline import Pipeline
from afsgap.search.base import build_backend
from afsgap.search.duckduckgo import DuckDuckGoBackend, _unwrap
from afsgap.sources.offline import OfflineConfluenceClient

from .fake_search import FakeSearchBackend
from .ollama_stub import OllamaStub


@pytest.fixture
def local(tmp_path, fixtures_dir):
    """A pipeline wired to the stub Ollama and fixture search, with pages cached."""
    with OllamaStub(fixtures_dir) as stub:
        settings = Settings(
            cache_dir=tmp_path / "cache",
            output_dir=tmp_path / "out",
            ollama_host=stub.host,
            ollama_model=stub.model,
        )
        settings.ensure_dirs()
        seed_page_cache(fixtures_dir, settings.cache_dir)   # fetch_text serves these from cache
        search = FakeSearchBackend(fixtures_dir)
        client = OllamaClient(settings, search)
        yield stub, settings, search, client


# -- schema handling --------------------------------------------------------
def test_schema_is_self_contained():
    flat = json.dumps(flatten_schema(SapStandardProcess.model_json_schema()))
    assert "$ref" not in flat and "$defs" not in flat


def test_nested_models_survive_flattening():
    flat = flatten_schema(GapAnalysis.model_json_schema())
    gap = flat["properties"]["gaps"]["items"]["properties"]
    assert "severity" in gap and "evidence" in gap


def test_decoration_is_stripped():
    flat = json.dumps(flatten_schema(SapStandardProcess.model_json_schema()))
    assert '"title"' not in flat


# -- client behaviour -------------------------------------------------------
def test_model_presence_is_checked(local):
    _, settings, search, client = local
    client.check()
    settings.ollama_model = "not-pulled:70b"
    with pytest.raises(OllamaUnavailableError, match="not pulled"):
        OllamaClient(settings, search).check()


def test_unreachable_host_is_explained(tmp_path, fixtures_dir):
    settings = Settings(ollama_host="http://127.0.0.1:1", cache_dir=tmp_path)
    with pytest.raises(OllamaUnavailableError, match="ollama serve"):
        OllamaClient(settings, FakeSearchBackend(fixtures_dir)).check()


def test_extract_sends_the_schema_as_format(local):
    stub, _, _, client = local
    client.extract(system="s", prompt="p", schema=SapStandardProcess)
    request = stub.chats[-1]
    assert request["stream"] is True, "responses must stream so slow models are not killed"
    assert "standard_process_flow" in request["format"]["properties"]
    assert request["options"]["temperature"] == 0
    assert request["keep_alive"], "the model should stay resident between stages"


def test_invalid_json_is_retried_then_succeeds(tmp_path, fixtures_dir):
    with OllamaStub(fixtures_dir, bad_responses=1) as stub:
        settings = Settings(cache_dir=tmp_path, ollama_host=stub.host, ollama_model=stub.model)
        client = OllamaClient(settings, FakeSearchBackend(fixtures_dir))
        result = client.extract(system="s", prompt="p", schema=SapStandardProcess)
        assert result.process_name
        assert len(stub.chats) == 2, "the first bad reply should have been retried"


def test_persistent_bad_json_fails_loudly(tmp_path, fixtures_dir):
    with OllamaStub(fixtures_dir, bad_responses=99) as stub:
        settings = Settings(cache_dir=tmp_path, ollama_host=stub.host,
                            ollama_model=stub.model, ollama_max_attempts=2)
        client = OllamaClient(settings, FakeSearchBackend(fixtures_dir))
        with pytest.raises(RuntimeError, match="could not produce valid"):
            client.extract(system="s", prompt="p", schema=SapStandardProcess)


def test_research_retrieves_without_calling_the_model(local):
    stub, _, search, client = local
    transcript = client.research(
        system="s", prompt="p",
        queries=["SAP returns process flow", "SAP EWM returns"],
        allowed_domains=["help.sap.com"],
    )
    assert not stub.chats, "retrieval must not spend a model call"
    assert transcript.hits and transcript.pages
    assert all("help.sap.com" in url for url in transcript.pages)
    assert "SOURCE:" in transcript.text
    assert search.queries == ["SAP returns process flow", "SAP EWM returns"]


def test_research_without_queries_retrieves_nothing(local):
    _, _, _, client = local
    transcript = client.research(system="s", prompt="p", queries=[])
    assert transcript.stop_reason == "no_queries"
    assert not transcript.hits


def test_long_prompts_are_trimmed_not_dropped(local):
    stub, settings, _, client = local
    settings.local_prompt_chars = 500
    client.extract(system="s", prompt="x" * 5000, schema=SapStandardProcess)
    sent = stub.chats[-1]["messages"][-1]["content"]
    assert len(sent) < 1000 and "omitted" in sent


def test_trimming_keeps_the_instruction_at_the_end(local):
    """Cutting the tail removes the ask - the bug that made a stage produce
    evidence with no instruction telling the model what to do with it."""
    stub, settings, _, client = local
    settings.local_prompt_chars = 600
    prompt = "EVIDENCE " + ("x" * 4000) + "\nProduce the structured SAP standard record."
    client.extract(system="s", prompt=prompt, schema=SapStandardProcess)
    sent = stub.chats[-1]["messages"][-1]["content"]
    assert sent.startswith("EVIDENCE"), "the start of the prompt must survive"
    assert sent.rstrip().endswith("Produce the structured SAP standard record."), \
        "the closing instruction must survive"


def test_evidence_budget_is_published_for_researchers(local):
    _, settings, _, client = local
    assert 0 < client.evidence_char_budget < settings.local_prompt_chars


# -- end to end -------------------------------------------------------------
def test_full_local_run_produces_a_document(local, fixtures_dir):
    _, settings, _, client = local
    pipeline = Pipeline(settings, client, use_cache=False)
    result, paths = pipeline.run(
        "Defective Parts Return", confluence=OfflineConfluenceClient(fixtures_dir)
    )
    assert result.validation.passed, result.validation.errors
    assert result.gap_analysis.gaps
    assert paths["markdown"].exists()
    # the guards still hold on the local path
    assert {c.tcode for c in result.sap_research.verified_tcodes} == {"VA01", "VL01N"}
    assert {c.tcode for c in result.sap_research.dropped_tcodes} == {"ZRET9", "MIGO"}
    assert any(e.rule == "tolerance" for e in result.validation.exclusions)


def test_local_run_verifies_quotes_against_retrieved_pages(local, fixtures_dir):
    _, settings, _, client = local
    pipeline = Pipeline(settings, client, use_cache=False)
    result, _ = pipeline.run("Defective Parts Return", confluence=OfflineConfluenceClient(fixtures_dir))
    # The industry fixture cites a page this run never retrieved, so its quotes
    # must be dropped rather than published as citations.
    assert any(e.rule == "unverified_quote" for e in result.validation.exclusions)
    surviving = [
        e.url
        for practice in result.industry_research.benchmark.leading_practices
        for e in practice.evidence
    ]
    assert all("mckinsey.com" not in url for url in surviving)


# -- search backend ---------------------------------------------------------
def test_backend_factory():
    assert build_backend(Settings(), "duckduckgo").name == "duckduckgo"
    assert build_backend(Settings(), "none").name == "none"
    with pytest.raises(ValueError, match="Unknown search backend"):
        build_backend(Settings(), "bing")


def test_redirect_urls_are_unwrapped():
    wrapped = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fhelp.sap.com%2Fdocs%2Fx&rut=abc"
    assert _unwrap(wrapped) == "https://help.sap.com/docs/x"
    assert _unwrap("https://help.sap.com/docs/y") == "https://help.sap.com/docs/y"


def test_site_operators_steer_but_do_not_replace_filtering(monkeypatch):
    backend = DuckDuckGoBackend(Settings(search_pause_seconds=0))
    seen: list[str] = []

    def fake_one(query, max_results):
        seen.append(query)
        from afsgap.search.base import SearchResult

        return [
            SearchResult(url="https://help.sap.com/docs/a", title="SAP"),
            SearchResult(url="https://randomblog.example.com/b", title="Blog"),
        ]

    monkeypatch.setattr(backend, "_one", fake_one)
    results = backend.search("returns process", allowed_domains=["help.sap.com", "community.sap.com"])
    assert any("site:help.sap.com" in query for query in seen), "should steer the engine"
    assert seen[-1] == "returns process", "and still run the bare query"
    assert [r.url for r in results] == ["https://help.sap.com/docs/a"], "off-allowlist results dropped"


def test_null_backend_returns_nothing():
    assert build_backend(Settings(), "none").search("anything") == []


# -- slow-model handling ----------------------------------------------------
def test_responses_stream_so_slow_models_are_not_killed(local):
    """A wall-clock timeout on a non-streaming call is what caused the original
    900s failure; the read timeout must apply between chunks instead."""
    stub, settings, _, client = local
    client.extract(system="s", prompt="p", schema=SapStandardProcess)
    assert stub.chats[-1]["stream"] is True
    assert settings.ollama_chunk_timeout < settings.ollama_timeout


def test_model_is_preloaded_once_not_per_stage(local):
    stub, _, _, client = local
    for _ in range(3):
        client.extract(system="s", prompt="p", schema=SapStandardProcess)
    preloads = [r for r in stub.requests if "prompt" in r and "messages" not in r]
    assert len(preloads) == 1, "reloading the model each stage can cost more than generation"


def test_compact_instruction_limits_output_length(local):
    stub, settings, _, client = local
    client.extract(system="SYSTEM", prompt="p", schema=SapStandardProcess)
    system = stub.chats[-1]["messages"][0]["content"]
    assert "LENGTH BUDGET" in system
    assert str(settings.local_max_items) in system


def test_compact_instruction_can_be_turned_off(local):
    stub, settings, _, client = local
    settings.local_compact = False
    client.extract(system="SYSTEM", prompt="p", schema=SapStandardProcess)
    assert "LENGTH BUDGET" not in stub.chats[-1]["messages"][0]["content"]


def test_generation_is_capped(local):
    stub, settings, _, client = local
    client.extract(system="s", prompt="p", schema=SapStandardProcess)
    assert stub.chats[-1]["options"]["num_predict"] == settings.ollama_num_predict


def test_benchmark_reports_a_rate(local):
    _, _, _, client = local
    result = client.benchmark()
    assert result["tokens"] > 0
    assert result["tokens_per_second"] > 0
    assert "load_seconds" in result


def test_defaults_fit_a_laptop(local):
    """The original defaults asked a CPU-bound model for ~10k tokens of input."""
    _, settings, _, _ = local
    assert settings.local_prompt_chars <= 16000
    assert settings.local_max_pages * settings.local_page_chars <= 16000
    assert settings.ollama_num_ctx <= 8192
