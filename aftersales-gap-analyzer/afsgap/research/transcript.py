"""What a research stage retrieved, independent of which backend retrieved it.

Both the hosted path (Claude's server-side web search) and the local path
(DuckDuckGo + page fetch) produce one of these, so everything downstream -
filtering, extraction, verification, the evidence register - is identical.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SearchHit:
    url: str
    title: str = ""
    query: str = ""
    snippet: str = ""


@dataclass
class Citation:
    url: str
    title: str = ""
    cited_text: str = ""


@dataclass
class ResearchTranscript:
    text: str = ""
    hits: list[SearchHit] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    stop_reason: str = ""
    # url -> retrieved page text, used to verify quotes literally
    pages: dict[str, str] = field(default_factory=dict)

    def evidence_block(self, limit: int = 120, max_chars: int | None = None) -> str:
        """Render retrieved material as prompt text for the extraction call.

        When a budget is given the sections are trimmed by value, not by
        position. The retrieved page text is what findings are actually made
        from, so it keeps most of the budget; the list of URLs is the first
        thing cut. Trimming from the end of the assembled string instead would
        throw away the page content and keep the index of it.
        """
        if max_chars is None:
            return self._assemble(self.hits[:limit], self.citations[:limit], self.text)

        narrative_budget = int(max_chars * 0.75)
        citation_budget = int(max_chars * 0.15)

        narrative = self.text
        if len(narrative) > narrative_budget:
            narrative = narrative[:narrative_budget] + "\n[... further retrieved text omitted ...]"

        citations: list[Citation] = []
        used = 0
        for citation in self.citations:
            cost = len(citation.cited_text) + len(citation.url) + 8
            if used + cost > citation_budget:
                break
            citations.append(citation)
            used += cost

        remaining = max(max_chars - len(narrative) - used, 400)
        hits: list[SearchHit] = []
        used = 0
        for hit in self.hits[:limit]:
            cost = len(hit.url) + len(hit.title) + 16
            if used + cost > remaining:
                break
            hits.append(hit)
            used += cost

        return self._assemble(hits, citations, narrative, brief=True)

    def _assemble(
        self,
        hits: list[SearchHit],
        citations: list[Citation],
        narrative: str,
        brief: bool = False,
    ) -> str:
        lines = ["## Retrieved sources", ""]
        for hit in hits:
            lines.append(f"- URL: {hit.url}")
            if hit.title:
                lines.append(f"  TITLE: {hit.title}")
            if hit.snippet and not brief:
                lines.append(f"  SNIPPET: {hit.snippet}")
        if citations:
            lines += ["", "## Verbatim citations", ""]
            for citation in citations:
                lines.append(f'- "{citation.cited_text}" -- {citation.url}')
        lines += ["", "## Research narrative", "", narrative]
        return "\n".join(lines)
