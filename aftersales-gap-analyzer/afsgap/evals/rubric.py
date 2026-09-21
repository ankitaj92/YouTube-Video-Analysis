"""Where the expected answers come from.

Most of the ground truth is already written down. A process definition records
the problems the business knows it has and the custom objects it is carrying;
a gap analysis that ignores them is a bad analysis, whatever else it says. So
the rubric is derived from the inputs rather than invented:

* every known pain point must be addressed by at least one gap;
* every custom object must receive an explicit S/4HANA disposition.

That matters for honesty as much as for effort. Hand-authoring "the right
answer" for an SAP process means asserting SAP facts, which is exactly what
this tool refuses to do without a source. Deriving expectations from the
client's own description asserts nothing - it only asks whether the analysis
engaged with what it was given.

Hand-authored rubric items remain available in the case file for expectations
that cannot be derived, and those are the user's to write and correct.
"""

from __future__ import annotations

import re

from ..models import CurrentProcess
from .schema import RubricItem

_WORD = re.compile(r"[a-z0-9]+")
STOPWORDS = {
    "the", "a", "an", "of", "for", "and", "to", "in", "is", "are", "no", "not", "with",
    "that", "this", "it", "its", "on", "by", "from", "as", "at", "so", "be", "has", "have",
    "which", "into", "between", "there", "their", "them", "than", "then", "when", "each",
}


def keywords_from(text: str, limit: int = 6) -> list[str]:
    """Distinctive words from a sentence, for the no-judge heuristic."""
    seen: list[str] = []
    for word in _WORD.findall((text or "").lower()):
        if len(word) <= 3 or word in STOPWORDS or word in seen:
            continue
        seen.append(word)
    return seen[:limit]


def derive_rubric(process: CurrentProcess | None) -> list[RubricItem]:
    """Build the expectations implied by the process definition itself."""
    if process is None:
        return []

    items: list[RubricItem] = []

    for index, pain in enumerate(process.known_pain_points, start=1):
        if not pain.strip():
            continue
        items.append(
            RubricItem(
                id=f"pain{index:02d}",
                family="insight",
                description=f"Addresses the known pain point: {pain}",
                keywords=keywords_from(pain),
                must=True,
                source="derived_pain_point",
            )
        )

    # Pain points recorded against individual steps count too - they are often
    # more specific than the summary list.
    for step in process.steps:
        for index, pain in enumerate(step.pain_points, start=1):
            if not pain.strip():
                continue
            items.append(
                RubricItem(
                    id=f"step{step.seq:02d}pain{index}",
                    family="insight",
                    description=f"Addresses the '{step.name}' problem: {pain}",
                    keywords=keywords_from(pain),
                    must=False,      # step-level detail: valued, not blocking
                    source="derived_pain_point",
                )
            )

    for index, obj in enumerate(process.custom_objects, start=1):
        name = obj.split()[0].split("-")[0].strip() if obj else ""
        if not name:
            continue
        items.append(
            RubricItem(
                id=f"custom{index:02d}",
                family="insight",
                description=f"Gives the custom object {name} an explicit S/4HANA disposition",
                keywords=[name.lower()],
                must=True,
                source="derived_custom_object",
            )
        )

    return items
