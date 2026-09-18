"""Source credibility classification."""

from __future__ import annotations

from urllib.parse import urlparse

from ..models import Source, SourceTier
from ..resources_loader import load_resource


def domain_of(url: str) -> str:
    netloc = urlparse(url if "//" in url else f"https://{url}").netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc.split(":")[0]


def _matches(domain: str, allowed: str) -> bool:
    return domain == allowed or domain.endswith("." + allowed)


class SourceClassifier:
    def __init__(self, resource_dir: str | None = None) -> None:
        data = load_resource("sources", resource_dir)
        self.sap_official: list[str] = list(data.get("sap_official", []))
        self.industry: dict[str, list[str]] = dict(data.get("industry_credible", {}))
        self.scholarly: list[str] = list(data.get("scholarly", []))
        self.denied: list[str] = list(data.get("denied", []))

    # -- classification ----------------------------------------------------
    def classify(self, url: str) -> tuple[SourceTier, str]:
        domain = domain_of(url)
        if not domain:
            return "other", ""
        if any(_matches(domain, d) for d in self.denied):
            return "denied", ""
        if any(_matches(domain, d) for d in self.sap_official):
            return "sap_official", "sap"
        for category, domains in self.industry.items():
            if any(_matches(domain, d) for d in domains):
                return "industry_credible", category
        if any(_matches(domain, d) for d in self.scholarly):
            return "scholarly", "scholarly"
        return "other", ""

    def describe(self, url: str, title: str = "", query: str = "", snippet: str = "") -> Source:
        tier, category = self.classify(url)
        return Source(
            url=url, title=title, domain=domain_of(url), tier=tier,
            category=category, retrieved_query=query, snippet=snippet,
        )

    # -- predicates --------------------------------------------------------
    def is_sap_official(self, url: str) -> bool:
        return self.classify(url)[0] == "sap_official"

    def is_credible_industry(self, url: str) -> bool:
        return self.classify(url)[0] in {"industry_credible", "scholarly"}

    def split_industry(self, sources: list[Source]) -> tuple[list[Source], list[Source]]:
        """Filter a normal (unrestricted) search result set against the allowlist."""
        kept = [s for s in sources if s.tier in {"industry_credible", "scholarly"}]
        rejected = [s for s in sources if s.tier not in {"industry_credible", "scholarly"}]
        return kept, rejected
