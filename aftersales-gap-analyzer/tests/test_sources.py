"""Industry research searches normally, then filters against the allowlist."""

import pytest

from afsgap.filters.sources import domain_of
from afsgap.models import Source


@pytest.mark.parametrize(
    "url,tier",
    [
        ("https://help.sap.com/docs/x", "sap_official"),
        ("https://community.sap.com/t5/blogs/y", "sap_official"),
        ("https://www.mckinsey.com/industries/automotive", "industry_credible"),
        ("https://www.apqc.org/resource-library/z", "industry_credible"),
        ("https://www.automotivelogistics.media/a", "industry_credible"),
        ("https://link.springer.com/article/1", "scholarly"),
        ("https://www.reddit.com/r/sap", "denied"),
        ("https://somerandomblog.example.net/post", "other"),
    ],
)
def test_classification(classifier, url, tier):
    assert classifier.classify(url)[0] == tier


def test_subdomains_inherit_the_tier(classifier):
    assert classifier.classify("https://blogs.help.sap.com/page")[0] == "sap_official"


def test_domain_normalisation():
    assert domain_of("https://www.mckinsey.com/a/b") == "mckinsey.com"
    assert domain_of("help.sap.com/docs") == "help.sap.com"


def test_split_industry_keeps_only_credible(classifier):
    sources = [
        classifier.describe("https://www.bcg.com/publications/a"),
        classifier.describe("https://www.reddit.com/r/logistics"),
        classifier.describe("https://randomsite.example.com/post"),
        classifier.describe("https://doi.org/10.1000/x"),
    ]
    kept, rejected = classifier.split_industry(sources)
    assert {s.domain for s in kept} == {"bcg.com", "doi.org"}
    assert {s.domain for s in rejected} == {"reddit.com", "randomsite.example.com"}


def test_sap_facts_may_only_come_from_sap(classifier):
    assert classifier.is_sap_official("https://help.sap.com/docs/x")
    assert not classifier.is_sap_official("https://www.mckinsey.com/x")
    assert not classifier.is_sap_official("https://sap-press.example.com/x")
