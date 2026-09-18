"""Shared machinery for pattern-based content filters."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from .segmentation import rejoin, segment


@dataclass
class ScrubResult:
    text: str
    removed: list[tuple[str, list[str]]] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.removed)


class PatternFilter:
    """Detects and removes segments matching any configured pattern.

    ``allowances`` are patterns that explain away a match: if the only reason a
    segment matched is an allowance phrase, the segment is kept.
    """

    rule_name = "pattern"

    def __init__(
        self,
        patterns: Iterable[re.Pattern[str]],
        allowances: Iterable[re.Pattern[str]] = (),
    ) -> None:
        self.patterns = list(patterns)
        self.allowances = list(allowances)

    # -- detection ---------------------------------------------------------
    def matches(self, text: str) -> list[str]:
        """Return the matched substrings in ``text`` (empty when clean)."""
        if not text:
            return []
        hits: list[str] = []
        for pattern in self.patterns:
            for found in pattern.finditer(text):
                token = found.group(0)
                if self._is_allowed(text, found.start(), found.end()):
                    continue
                hits.append(token)
        return hits

    def is_clean(self, text: str) -> bool:
        return not self.matches(text)

    def _is_allowed(self, text: str, start: int, end: int) -> bool:
        for allowance in self.allowances:
            for spared in allowance.finditer(text):
                if spared.start() <= start and spared.end() >= end:
                    return True
        return False

    # -- removal -----------------------------------------------------------
    def scrub(self, text: str) -> ScrubResult:
        if not text:
            return ScrubResult(text="", removed=[])
        segments = segment(text)
        kept: list[str] = []
        removed: list[tuple[str, list[str]]] = []
        for seg in segments:
            hits = self.matches(seg)
            if hits:
                removed.append((seg.strip(), sorted(set(h.lower() for h in hits))))
            else:
                kept.append(seg)
        return ScrubResult(text=rejoin(kept, text), removed=removed)

    def scrub_structure(self, value: Any, path: str = "") -> tuple[Any, list[tuple[str, str, list[str]]]]:
        """Recursively scrub strings inside dicts/lists.

        Returns the cleaned structure and a list of ``(path, text, terms)``.
        List entries that become empty are dropped entirely; a scalar string
        that becomes empty is returned as an empty string so the caller can
        decide whether the parent object is still meaningful.
        """
        removals: list[tuple[str, str, list[str]]] = []

        if isinstance(value, str):
            result = self.scrub(value)
            for text, terms in result.removed:
                removals.append((path, text, terms))
            return result.text, removals

        if isinstance(value, list):
            cleaned_list: list[Any] = []
            for index, item in enumerate(value):
                cleaned, sub = self.scrub_structure(item, f"{path}[{index}]")
                removals.extend(sub)
                if isinstance(cleaned, str) and not cleaned.strip():
                    continue
                if isinstance(cleaned, (dict, list)) and not cleaned:
                    continue
                cleaned_list.append(cleaned)
            return cleaned_list, removals

        if isinstance(value, dict):
            cleaned_dict: dict[str, Any] = {}
            for key, item in value.items():
                cleaned, sub = self.scrub_structure(item, f"{path}.{key}" if path else str(key))
                removals.extend(sub)
                cleaned_dict[key] = cleaned
            return cleaned_dict, removals

        return value, removals
