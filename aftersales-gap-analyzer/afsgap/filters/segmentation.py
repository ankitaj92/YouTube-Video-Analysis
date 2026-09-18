"""Segment text so a filter can remove an offending sentence without destroying
the paragraph around it."""

from __future__ import annotations

import re

_SENTENCE_END = re.compile(r"(?<=[.!?;])\s+")


def segment(text: str) -> list[str]:
    """Split into the smallest units worth keeping or dropping.

    Lines are preserved first (bullets, table rows, numbered steps), then each
    line is split into sentences. Empty lines are kept so rejoining round-trips.
    """
    segments: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            segments.append(line)
            continue
        parts = _SENTENCE_END.split(line)
        segments.extend(part for part in parts if part != "")
    return segments


def rejoin(segments: list[str], original: str) -> str:
    """Rejoin surviving segments, keeping line structure roughly intact."""
    if "\n" not in original:
        return " ".join(s.strip() for s in segments if s.strip()).strip()
    out: list[str] = []
    buffer: list[str] = []
    for seg in segments:
        if seg.strip() == "" and not seg.startswith(" "):
            out.append(" ".join(buffer).strip())
            buffer = []
            out.append("")
        else:
            buffer.append(seg.strip())
    if buffer:
        out.append(" ".join(buffer).strip())
    return "\n".join(out).strip()
