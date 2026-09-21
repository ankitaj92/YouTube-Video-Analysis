"""Search backends and the fallback chain."""

import pytest

from afsgap.config import Settings
from afsgap.search.base import KNOWN_BACKENDS, _build_one, build_backend
from afsgap.search.chain import ChainBackend
from afsgap.search.seeds import SeedsBackend


class Stub:
    def __init__(self, name, results=None, explode=False):
        self.name = name
        self._results = results or []
        self._explode = explode
        self.calls = 0

    def search(self, query, max_results=8, allowed_domains=None):
        self.calls += 1
        if self._explode:
            raise RuntimeError(f"{self.name} is blocked")
        return list(self._results)


from afsgap.search.base import SearchResult  # noqa: E402

HIT = SearchResult(url="https://help.sap.com/docs/x", title="Returns")


# -- chain ------------------------------------------------------------------
def test_first_working_backend_wins():
    first, second = Stub("a", [HIT]), Stub("b", [HIT])
    assert ChainBackend([first, second]).search("q") == [HIT]
    assert second.calls == 0, "the fallback should not be consulted unnecessarily"


def test_empty_results_fall_through():
    blocked, fallback = Stub("blocked", []), Stub("fallback", [HIT])
    assert ChainBackend([blocked, fallback]).search("q") == [HIT]
    assert blocked.calls == 1 and fallback.calls == 1


def test_a_raising_backend_does_not_end_the_run():
    broken, fallback = Stub("broken", explode=True), Stub("fallback", [HIT])
    assert ChainBackend([broken, fallback]).search("q") == [HIT]


def test_everything_blocked_returns_nothing_rather_than_failing():
    assert ChainBackend([Stub("a", []), Stub("b", explode=True)]).search("q") == []


def test_chain_name_shows_the_order():
    assert ChainBackend([Stub("duckduckgo"), Stub("seeds")]).name == "duckduckgo+seeds"


# -- factory ----------------------------------------------------------------
def test_comma_separated_list_builds_a_chain():
    backend = build_backend(Settings(), "duckduckgo,mojeek,seeds")
    assert isinstance(backend, ChainBackend)
    assert backend.name == "duckduckgo+mojeek+seeds"


def test_single_backend_is_not_wrapped():
    assert not isinstance(build_backend(Settings(), "mojeek"), ChainBackend)


def test_unavailable_backend_is_skipped_in_a_chain():
    # searxng without a URL cannot be built, but must not take the chain down
    backend = build_backend(Settings(searxng_url=""), "searxng,duckduckgo")
    assert "duckduckgo" in backend.name


def test_unknown_backend_lists_the_options():
    with pytest.raises(ValueError, match="duckduckgo"):
        build_backend(Settings(), "google")


def test_every_known_backend_can_be_named():
    for name in KNOWN_BACKENDS:
        if name == "searxng":
            continue          # needs a URL
        assert _build_one(Settings(), name).name


def test_searxng_explains_the_missing_url():
    with pytest.raises(ValueError, match="AFSGAP_SEARXNG_URL"):
        _build_one(Settings(searxng_url=""), "searxng")


# -- seeds ------------------------------------------------------------------
@pytest.fixture
def seed_file(tmp_path):
    path = tmp_path / "seeds.yaml"
    path.write_text(
        """
sources:
  - url: https://help.sap.com/docs/returns
    title: Returns Processing
    topics: [returns, defective]
  - url: https://help.sap.com/docs/warehouse
    title: Inbound processing
    topics: [warehouse, inbound]
  - url: https://community.sap.com/general
    title: General entry point
  - url: https://randomblog.example.com/post
    title: Not allowlisted
    topics: [returns]
""",
        encoding="utf-8",
    )
    return path


def test_seeds_match_by_topic(seed_file):
    backend = SeedsBackend(Settings(seed_sources_path=str(seed_file)))
    urls = [r.url for r in backend.search("defective parts returns process")]
    assert urls[0] == "https://help.sap.com/docs/returns"
    assert "https://help.sap.com/docs/warehouse" not in urls


def test_untopiced_seeds_are_always_eligible_but_rank_lower(seed_file):
    backend = SeedsBackend(Settings(seed_sources_path=str(seed_file)))
    urls = [r.url for r in backend.search("returns")]
    assert "https://community.sap.com/general" in urls
    assert urls.index("https://help.sap.com/docs/returns") < urls.index("https://community.sap.com/general")


def test_seeds_respect_the_domain_allowlist(seed_file):
    backend = SeedsBackend(Settings(seed_sources_path=str(seed_file)))
    urls = [r.url for r in backend.search("returns", allowed_domains=["help.sap.com"])]
    assert urls == ["https://help.sap.com/docs/returns"]


def test_missing_seed_file_is_not_an_error(tmp_path):
    backend = SeedsBackend(Settings(seed_sources_path=str(tmp_path / "nope.yaml")))
    assert backend.search("anything") == []


def test_plain_url_strings_are_accepted(tmp_path):
    path = tmp_path / "seeds.yaml"
    path.write_text("sources:\n  - https://help.sap.com/docs/a\n", encoding="utf-8")
    backend = SeedsBackend(Settings(seed_sources_path=str(path)))
    assert [r.url for r in backend.search("anything")] == ["https://help.sap.com/docs/a"]


def test_shipped_seed_file_is_empty_by_default():
    """Seeds are the user's own sources - nothing is guessed on their behalf."""
    assert SeedsBackend(Settings()).entries == []


# -- ddgs 9.x semantics ------------------------------------------------------
from afsgap.search.duckduckgo import (  # noqa: E402
    ENCYCLOPAEDIC_ENGINES,
    DuckDuckGoBackend,
    available_engines,
    resolve_engines,
)


def test_encyclopaedic_engines_are_not_in_the_default_list():
    """ddgs 'auto' leads with Wikipedia/Grokipedia and ranks wikipedia.org top -
    the wrong answer when researching SAP documentation."""
    configured = set(Settings().ddgs_backends.split(","))
    assert not (configured & ENCYCLOPAEDIC_ENGINES)
    assert "duckduckgo" in configured
    assert "auto" not in configured


def test_configured_engines_exist_in_the_installed_library():
    installed = available_engines()
    if not installed:
        pytest.skip("search library not installed")
    for name in Settings().ddgs_backends.split(","):
        assert name in installed, f"{name} is not an engine this ddgs version offers"


def test_unknown_engines_fall_back_rather_than_returning_nothing():
    resolved = resolve_engines("bing,yandex")
    assert resolved, "an all-unknown list must not resolve to an empty engine list"
    assert not (set(resolved.split(",")) & ENCYCLOPAEDIC_ENGINES)


def test_engine_list_is_pinned_on_every_query(monkeypatch):
    backend = DuckDuckGoBackend(Settings(search_pause_seconds=0))
    if backend._client is None or backend._legacy:
        pytest.skip("new ddgs not installed")
    seen = {}

    class FakeSession:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def text(self, query, **kwargs):
            seen.update(kwargs)
            return []

    monkeypatch.setattr(backend, "_client", lambda **kw: FakeSession())
    backend.search("anything")
    assert seen.get("backend") == backend.engines


def test_empty_results_are_not_treated_as_a_failure(monkeypatch, caplog):
    """ddgs raises DDGSException('No results found.') instead of returning []."""
    backend = DuckDuckGoBackend(Settings(search_pause_seconds=0))

    def raise_empty(*a, **k):
        raise RuntimeError("No results found.")

    monkeypatch.setattr(backend, "_via_package", raise_empty)
    monkeypatch.setattr(backend, "_via_html", lambda *a, **k: [])
    with caplog.at_level("WARNING"):
        assert backend.search("anything") == []
    assert "No results found" not in caplog.text, "an empty result set is not a warning"


def test_domain_filtering_over_fetches_first(monkeypatch):
    """Asking for 8 and filtering to one domain usually leaves nothing, so the
    engine must be asked for more than the caller wants."""
    backend = DuckDuckGoBackend(Settings(search_pause_seconds=0, search_overfetch=4))
    asked: list[int] = []

    def fake_one(query, max_results):
        asked.append(max_results)
        return []

    monkeypatch.setattr(backend, "_one", fake_one)
    backend.search("q", max_results=8, allowed_domains=["help.sap.com"])
    assert asked and all(n > 8 for n in asked), f"expected over-fetch, got {asked}"


def test_no_over_fetch_without_domain_restriction(monkeypatch):
    backend = DuckDuckGoBackend(Settings(search_pause_seconds=0))
    asked: list[int] = []
    monkeypatch.setattr(backend, "_one", lambda q, n: asked.append(n) or [])
    backend.search("q", max_results=8)
    assert asked == [8]


def test_legacy_package_is_called_without_the_backend_argument(monkeypatch):
    """The older duckduckgo_search has no 'backend' parameter."""
    backend = DuckDuckGoBackend(Settings(search_pause_seconds=0))
    backend._legacy = True
    seen = {}

    class FakeSession:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def text(self, query, **kwargs):
            seen.update(kwargs)
            return [{"href": "https://help.sap.com/x", "title": "T", "body": "B"}]

    monkeypatch.setattr(backend, "_client", lambda *a, **k: FakeSession())
    results = backend.search("q")
    assert "backend" not in seen
    assert results[0].url == "https://help.sap.com/x"


def test_result_keys_from_either_library_are_handled(monkeypatch):
    backend = DuckDuckGoBackend(Settings(search_pause_seconds=0))

    class FakeSession:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def text(self, query, **kwargs):
            return [
                {"href": "https://a.example/1", "title": "A", "body": "body text"},
                {"url": "https://b.example/2", "title": "B", "description": "desc text"},
            ]

    monkeypatch.setattr(backend, "_client", lambda **kw: FakeSession())
    backend._legacy = False
    results = backend.search("q", max_results=5)
    assert [r.url for r in results] == ["https://a.example/1", "https://b.example/2"]
    assert results[0].snippet == "body text" and results[1].snippet == "desc text"
