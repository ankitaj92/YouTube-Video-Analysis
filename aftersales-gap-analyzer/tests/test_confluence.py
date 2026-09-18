"""Confluence reading: storage-format parsing, matching, auth and caching."""

import json

import pytest

from afsgap.config import Settings
from afsgap.sources.confluence import (
    ConfluenceClient,
    ConfluenceUnavailableError,
    title_similarity,
)
from afsgap.sources.offline import OfflineConfluenceClient
from afsgap.sources.storage_format import storage_to_text


# -- storage format ---------------------------------------------------------
def test_tables_survive_as_rows():
    storage = (
        "<table><tbody>"
        "<tr><th>Step</th><th>Actor</th></tr>"
        "<tr><td>Raise return</td><td>Dealer</td></tr>"
        "</tbody></table>"
    )
    text = storage_to_text(storage)
    assert "| Step | Actor |" in text
    assert "| Raise return | Dealer |" in text


def test_headings_and_lists_are_preserved():
    text = storage_to_text("<h2>Purpose</h2><ul><li>First</li><li>Second</li></ul>")
    assert "### Purpose" in text
    assert "- First" in text and "- Second" in text


def test_layout_macros_are_dropped():
    text = storage_to_text(
        '<p>Real content.</p><ac:structured-macro ac:name="toc">noise</ac:structured-macro>'
    )
    assert "Real content." in text
    assert "noise" not in text


def test_entities_and_nbsp_are_decoded():
    assert "credit & debit" in storage_to_text("<p>credit &amp; debit</p>")
    assert "\xa0" not in storage_to_text("<p>a&nbsp;b</p>")


def test_empty_storage_is_safe():
    assert storage_to_text("") == ""


# -- match scoring ----------------------------------------------------------
def test_exact_title_scores_top():
    assert title_similarity("Defective Parts Return", "Defective Parts Return Process") == 1.0


def test_unrelated_title_scores_zero():
    assert title_similarity("Defective Parts Return", "Travel Expense Policy") == 0.0


def test_partial_overlap_scores_between():
    score = title_similarity("Battery Pack Return", "Defective Parts Return Process")
    assert 0 < score < 0.45, "a loosely related page must not be treated as the process"


def test_scoring_ignores_noise_words():
    assert title_similarity("The Defective Parts Return Process", "Defective Parts Return") == 1.0


# -- client configuration ---------------------------------------------------
def test_missing_base_url_is_explained():
    with pytest.raises(ConfluenceUnavailableError, match="BASE_URL"):
        ConfluenceClient(Settings(confluence_base_url="", confluence_api_token="x"))


def test_missing_token_is_explained():
    with pytest.raises(ConfluenceUnavailableError, match="API_TOKEN"):
        ConfluenceClient(Settings(confluence_base_url="https://example.atlassian.net/wiki"))


def test_cloud_uses_basic_auth():
    client = ConfluenceClient(Settings(
        confluence_base_url="https://example.atlassian.net/wiki",
        confluence_email="me@example.com",
        confluence_api_token="token",
    ))
    assert client.session.auth == ("me@example.com", "token")
    assert "Authorization" not in client.session.headers


def test_data_center_uses_bearer_token():
    client = ConfluenceClient(Settings(
        confluence_base_url="https://confluence.example.com",
        confluence_api_token="pat",
        confluence_auth="bearer",
    ))
    assert client.session.headers["Authorization"] == "Bearer pat"


def test_page_is_cached_after_first_fetch(tmp_path, monkeypatch):
    settings = Settings(
        confluence_base_url="https://example.atlassian.net/wiki",
        confluence_email="me@example.com",
        confluence_api_token="token",
        cache_dir=tmp_path,
    )
    client = ConfluenceClient(settings)
    calls = {"n": 0}

    def fake_get(path, params):
        calls["n"] += 1
        return {
            "id": "42", "title": "Defective Parts Return",
            "space": {"key": "AFTS"},
            "version": {"number": 3, "when": "2025-01-01T00:00:00Z", "by": {"displayName": "Owner"}},
            "body": {"storage": {"value": "<p>Dealer returns the part.</p>"}},
            "_links": {"webui": "/spaces/AFTS/pages/42"},
        }

    monkeypatch.setattr(client, "_get", fake_get)
    first = client.get_page("42")
    second = client.get_page("42")
    assert calls["n"] == 1, "second read must come from the cache"
    assert first.text == second.text == "Dealer returns the part."
    assert first.url.endswith("/spaces/AFTS/pages/42")
    assert "v3" in first.citation() and "AFTS" in first.citation()


# -- offline client ---------------------------------------------------------
def test_offline_search_ranks_by_title(fixtures_dir):
    client = OfflineConfluenceClient(fixtures_dir)
    pages = client.search("Defective Parts Return")
    assert pages[0].title == "Defective Parts Return Process"
    assert pages[0].score == 1.0


def test_offline_search_respects_space_filter(fixtures_dir):
    client = OfflineConfluenceClient(fixtures_dir)
    assert client.search("Defective Parts Return", spaces=["OTHER"]) == []


def test_offline_get_page_returns_body(fixtures_dir):
    client = OfflineConfluenceClient(fixtures_dir)
    assert "Raise return request" in client.get_page("123456").text
