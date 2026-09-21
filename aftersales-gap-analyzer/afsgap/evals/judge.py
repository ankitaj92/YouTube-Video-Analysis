"""Scoring the rubric: heuristic by default, model-judged on request.

Two graders, deliberately:

* :class:`KeywordGrader` needs no model, costs nothing and is perfectly
  reproducible. It asks whether the distinctive words of an expectation appear
  anywhere in the analysis. It over-credits - a document can mention
  "inspection" without addressing the inspection problem - so treat it as a
  floor, and as the grader you run on every change.
* :class:`ModelGrader` asks a model whether each expectation was genuinely
  addressed, and requires it to quote the sentence that does so. The quote is
  then checked against the document, which makes the judge's answer falsifiable
  rather than a vibe.

Whatever the grader, an expectation is only ever scored against the analysis
itself, never against the source material - the question is what the tool
concluded, not what it read.
"""

from __future__ import annotations

import json
import logging
from typing import Protocol

from pydantic import BaseModel, Field

from .schema import CheckResult, RubricItem

logger = logging.getLogger(__name__)


class Grader(Protocol):
    name: str

    def grade(self, items: list[RubricItem], analysis_text: str) -> list[CheckResult]:
        ...


class KeywordGrader:
    """Cheap, deterministic, generous. The floor, not the ceiling."""

    name = "keywords"

    def __init__(self, min_ratio: float = 0.5) -> None:
        self.min_ratio = min_ratio

    def grade(self, items: list[RubricItem], analysis_text: str) -> list[CheckResult]:
        haystack = analysis_text.lower()
        results: list[CheckResult] = []
        for item in items:
            if not item.keywords:
                results.append(CheckResult(id=item.id, family=item.family, outcome="skipped",
                                           detail="no keywords to match on", must=item.must))
                continue
            found = [word for word in item.keywords if word.lower() in haystack]
            ratio = len(found) / len(item.keywords)
            outcome = "pass" if ratio >= self.min_ratio else ("partial" if found else "fail")
            results.append(CheckResult(
                id=item.id, family=item.family, outcome=outcome,
                detail=f"{len(found)}/{len(item.keywords)} keywords ({', '.join(found[:4])})",
                must=item.must,
            ))
        return results


class _Verdict(BaseModel):
    id: str
    addressed: bool
    quote: str = Field(default="", description="Verbatim sentence from the analysis that addresses it.")
    reason: str = ""


class _Verdicts(BaseModel):
    verdicts: list[_Verdict] = Field(default_factory=list)


JUDGE_SYSTEM = """You are grading a process gap analysis against a checklist of things it was \
expected to address.

For each checklist item, decide whether the analysis genuinely addresses it, and quote the exact \
sentence from the analysis that does so.

Rules:
- Quote verbatim from the analysis. If you cannot quote a sentence that addresses the item, it is \
not addressed - mentioning a topic in passing is not addressing it.
- A problem is addressed when the analysis identifies it, or recommends something that would \
resolve it. Restating it without consequence does not count.
- A custom object is dispositioned when the analysis says what happens to it (retire, keep, \
replace, extend), not merely that it exists.
- Judge only what the analysis says. Do not reward or penalise it for anything outside the text.
- Be strict. An eval that passes everything measures nothing."""


class ModelGrader:
    """Asks a model, then checks the model's own quote against the document."""

    name = "model"

    def __init__(self, client, batch_size: int = 12) -> None:
        self.client = client
        self.batch_size = batch_size

    def grade(self, items: list[RubricItem], analysis_text: str) -> list[CheckResult]:
        results: list[CheckResult] = []
        for start in range(0, len(items), self.batch_size):
            batch = items[start:start + self.batch_size]
            results.extend(self._grade_batch(batch, analysis_text))
        return results

    def _grade_batch(self, items: list[RubricItem], analysis_text: str) -> list[CheckResult]:
        checklist = "\n".join(f"- {item.id}: {item.description}" for item in items)
        prompt = (
            "## Checklist\n" + checklist + "\n\n"
            "## The analysis to grade\n" + analysis_text + "\n\n"
            "Return a verdict for every checklist id."
        )
        try:
            verdicts = self.client.extract(system=JUDGE_SYSTEM, prompt=prompt, schema=_Verdicts)
        except Exception as exc:
            logger.warning("The judge failed on a batch (%s); falling back to keywords.", exc)
            return KeywordGrader().grade(items, analysis_text)

        by_id = {verdict.id: verdict for verdict in verdicts.verdicts}
        normalised = " ".join(analysis_text.lower().split())
        results: list[CheckResult] = []
        for item in items:
            verdict = by_id.get(item.id)
            if verdict is None:
                results.append(CheckResult(id=item.id, family=item.family, outcome="fail",
                                           detail="the judge returned no verdict", must=item.must))
                continue
            if not verdict.addressed:
                results.append(CheckResult(id=item.id, family=item.family, outcome="fail",
                                           detail=verdict.reason[:160] or "not addressed",
                                           must=item.must))
                continue
            # A claim of "addressed" is only accepted with a quote that is
            # really in the document - otherwise the judge is guessing.
            quote = " ".join(verdict.quote.lower().split())
            if quote and quote in normalised:
                results.append(CheckResult(id=item.id, family=item.family, outcome="pass",
                                           detail=f'"{verdict.quote[:110]}"', must=item.must))
            else:
                results.append(CheckResult(
                    id=item.id, family=item.family, outcome="partial",
                    detail="judged addressed, but its supporting quote is not in the document",
                    must=item.must,
                ))
        return results


def build_grader(kind: str, settings=None):
    """kind: 'keywords' | 'claude' | 'ollama'."""
    kind = (kind or "keywords").lower()
    if kind in {"none", "keywords", "keyword"}:
        return KeywordGrader()
    if kind == "claude":
        from ..llm import ClaudeClient

        return ModelGrader(ClaudeClient(settings))
    if kind == "ollama":
        from ..llm.ollama import OllamaClient
        from ..search.base import build_backend

        return ModelGrader(OllamaClient(settings, build_backend(settings, "none")))
    raise ValueError(f"Unknown grader '{kind}'. Use keywords, claude or ollama.")
