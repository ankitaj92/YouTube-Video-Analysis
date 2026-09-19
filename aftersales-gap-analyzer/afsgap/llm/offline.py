"""Offline stand-in for :class:`ClaudeClient`.

Lets the whole pipeline - filters, verification, gap analysis, rendering - run
in PyCharm with no API key and no network, using fixtures. Used by the test
suite and by ``--offline`` on the CLI.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence, Type, TypeVar

from pydantic import BaseModel

from ..research.transcript import Citation, ResearchTranscript, SearchHit

T = TypeVar("T", bound=BaseModel)


class OfflineClient:
    """Serves canned research transcripts and canned structured extractions.

    Fixture layout (``fixtures_dir``)::

        research_sap.json          -> {"text":..., "hits":[...], "citations":[...]}
        research_industry.json
        SapStandardProcess.json    -> a valid SapStandardProcess payload
        IndustryBenchmark.json
        GapAnalysis.json
    """

    def __init__(self, fixtures_dir: Path) -> None:
        self.fixtures_dir = Path(fixtures_dir)
        self._research_calls = 0

    # -- research ----------------------------------------------------------
    def research(
        self,
        *,
        system: str,
        prompt: str,
        queries: Sequence[str] | None = None,
        allowed_domains: Sequence[str] | None = None,
        blocked_domains: Sequence[str] | None = None,
        max_searches: int = 10,
    ) -> ResearchTranscript:
        name = "research_sap" if allowed_domains else "research_industry"
        self._research_calls += 1
        payload = self._load(name)
        return ResearchTranscript(
            text=payload.get("text", ""),
            hits=[SearchHit(**hit) for hit in payload.get("hits", [])],
            citations=[Citation(**citation) for citation in payload.get("citations", [])],
            queries=payload.get("queries", []),
            stop_reason="end_turn",
        )

    # -- extraction --------------------------------------------------------
    def extract(self, *, system: str, prompt: str, schema: Type[T]) -> T:
        return schema.model_validate(self._load(schema.__name__))

    def _load(self, name: str) -> dict:
        path = self.fixtures_dir / f"{name}.json"
        if not path.exists():
            raise FileNotFoundError(
                f"Offline fixture {path} is missing. Run a live analysis once, or copy a "
                f"fixture from tests/fixtures/."
            )
        return json.loads(path.read_text(encoding="utf-8"))

    # -- offline extras ----------------------------------------------------
    offline = True

    def scholarly_works(self) -> list:
        """Canned OpenAlex results so offline runs need no network."""
        from ..models import ScholarlyWork

        path = self.fixtures_dir / "ScholarlyWorks.json"
        if not path.exists():
            return []
        return [ScholarlyWork.model_validate(item) for item in json.loads(path.read_text(encoding="utf-8"))]


def seed_page_cache(fixtures_dir: Path, cache_dir: Path) -> int:
    """Write ``pages.json`` into the page cache so T-code verification can run offline.

    Uses the same hashing scheme as the live fetcher, so the verifier finds the
    fixture page exactly where it would find a real one.
    """
    from ..research.http import _cache_path

    path = Path(fixtures_dir) / "pages.json"
    if not path.exists():
        return 0
    pages = json.loads(path.read_text(encoding="utf-8"))
    for url, text in pages.items():
        target = _cache_path(Path(cache_dir), url)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return len(pages)
