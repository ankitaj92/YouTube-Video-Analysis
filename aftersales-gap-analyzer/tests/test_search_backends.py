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
