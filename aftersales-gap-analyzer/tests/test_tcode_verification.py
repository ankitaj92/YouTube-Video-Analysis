"""A T-code reaches the document only with literal evidence from a public SAP page."""

import json

import pytest

from afsgap.filters.sources import SourceClassifier
from afsgap.models import TCodeCandidate
from afsgap.research.http import _cache_path
from afsgap.research.tcode import (
    TCodeVerifier,
    find_tcode_mentions,
    literal_match,
    looks_like_tcode,
)

SAP_URL = "https://help.sap.com/docs/example/returns"
PAGE = "Returns Processing. Use transaction VA01 to create the returns order. VL01N creates the delivery."


@pytest.fixture
def verifier(tmp_path, classifier):
    cache = tmp_path / "cache"
    path = _cache_path(cache, SAP_URL)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(PAGE, encoding="utf-8")
    return TCodeVerifier(classifier, cache_dir=cache)


@pytest.mark.parametrize("code", ["VA01", "VL01N", "MIGO", "MB1B", "/SCWM/PRDI"])
def test_valid_shapes(code):
    assert looks_like_tcode(code)


@pytest.mark.parametrize("code", ["SAP", "EWM", "CMH", "KPI", "RETURNSPROCESS", "AB", "S4HANA"])
def test_invalid_shapes(code):
    assert not looks_like_tcode(code)


def test_literal_match_requires_whole_token():
    assert literal_match(PAGE, "VA01")
    assert not literal_match(PAGE, "VA0")
    assert not literal_match("XVA01Y appears here", "VA01")


def test_code_on_official_page_is_verified(verifier):
    verified, dropped = verifier.verify([
        TCodeCandidate(tcode="VA01", source_url=SAP_URL, supporting_quote="Use transaction VA01")
    ])
    assert [c.tcode for c in verified] == ["VA01"]
    assert not dropped


def test_code_absent_from_the_page_is_dropped(verifier):
    verified, dropped = verifier.verify([TCodeCandidate(tcode="MIGO", source_url=SAP_URL)])
    assert not verified
    assert "not literally present" in dropped[0].reason


def test_non_sap_source_is_dropped(verifier):
    verified, dropped = verifier.verify([
        TCodeCandidate(tcode="VA01", source_url="https://en.wikipedia.org/wiki/SAP_ERP")
    ])
    assert not verified
    assert "not an official public SAP domain" in dropped[0].reason


def test_missing_source_is_dropped(verifier):
    verified, dropped = verifier.verify([TCodeCandidate(tcode="VA01")])
    assert not verified
    assert "no source URL" in dropped[0].reason


def test_unfetchable_page_is_dropped(verifier, monkeypatch):
    monkeypatch.setattr("afsgap.research.tcode.fetch_text", lambda *a, **k: "")
    verified, dropped = verifier.verify([
        TCodeCandidate(tcode="VA01", source_url="https://help.sap.com/docs/other/page")
    ])
    assert not verified
    assert "could not be retrieved" in dropped[0].reason


def test_duplicates_are_verified_once(verifier):
    verified, _ = verifier.verify([
        TCodeCandidate(tcode="VA01", source_url=SAP_URL),
        TCodeCandidate(tcode="va01", source_url=SAP_URL),
    ])
    assert len(verified) == 1


# -- output-side detection --------------------------------------------------
def test_mentions_need_a_cue():
    text = "Gap G01 concerns the ABAP report and its DOI. Use transaction VA01 to create the order."
    assert find_tcode_mentions(text) == {"VA01"}


def test_urls_never_produce_mentions():
    assert find_tcode_mentions("See https://help.sap.com/docs/SAP_S4HANA_ON-PREMISE/returns") == set()


def test_rejected_codes_are_hunted_without_a_cue():
    assert find_tcode_mentions("The MIGO posting stays manual.", also_search_for={"MIGO"}) == {"MIGO"}
