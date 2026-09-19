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

    def evidence_block(self, limit: int = 120) -> str:
        """Render retrieved material as prompt text for the extraction call."""
        lines = ["## Retrieved sources", ""]
        for hit in self.hits[:limit]:
            lines.append(f"- URL: {hit.url}")
            if hit.title:
                lines.append(f"  TITLE: {hit.title}")
            if hit.snippet:
                lines.append(f"  SNIPPET: {hit.snippet}")
        if self.citations:
            lines += ["", "## Verbatim citations", ""]
            for citation in self.citations[:limit]:
                lines.append(f'- "{citation.cited_text}" -- {citation.url}')
        lines += ["", "## Research narrative", "", self.text]
        return "\n".join(lines)
