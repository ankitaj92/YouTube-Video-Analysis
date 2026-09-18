"""Confluence storage format (XHTML) to readable text.

Process pages live and die by their tables - a step table converted to a wall of
run-together words is useless to the extraction stage. This parser keeps the
structure that carries meaning (headings, lists, table rows) and drops the rest,
using only the standard library.
"""

from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser

# Macros whose body is layout or noise rather than process content.
SKIPPED_MACROS = {"toc", "children", "pagetree", "excerpt-include", "info", "note", "expand-hidden"}

_BLANKS = re.compile(r"\n{3,}")


class _StorageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._row: list[str] = []
        self._cell: list[str] | None = None
        self._in_table = False
        self._skip_depth = 0
        self._list_depth = 0
        self._pending_list_item = False

    # -- helpers -----------------------------------------------------------
    def _emit(self, text: str) -> None:
        if self._cell is not None:
            self._cell.append(text)
        else:
            self.parts.append(text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag in {"script", "style"}:
            self._skip_depth += 1
            return
        if tag == "ac:structured-macro":
            if (attributes.get("ac:name") or "").lower() in SKIPPED_MACROS:
                self._skip_depth += 1
            return
        if self._skip_depth:
            return

        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            level = int(tag[1])
            self.parts.append("\n" + "#" * min(level + 1, 6) + " ")
        elif tag in {"ul", "ol"}:
            self._list_depth += 1
            self.parts.append("\n")
        elif tag == "li":
            self._pending_list_item = True
            self.parts.append("\n" + "  " * max(self._list_depth - 1, 0) + "- ")
        elif tag == "table":
            self._in_table = True
            self.parts.append("\n")
        elif tag == "tr":
            self._row = []
        elif tag in {"td", "th"}:
            self._cell = []
        elif tag in {"p", "div", "br"}:
            self._emit("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} or tag == "ac:structured-macro":
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return

        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("\n")
        elif tag in {"ul", "ol"}:
            self._list_depth = max(0, self._list_depth - 1)
            self.parts.append("\n")
        elif tag in {"td", "th"}:
            cell = " ".join("".join(self._cell or []).split())
            self._row.append(cell)
            self._cell = None
        elif tag == "tr":
            if any(cell for cell in self._row):
                self.parts.append("\n| " + " | ".join(self._row) + " |")
            self._row = []
        elif tag == "table":
            self._in_table = False
            self.parts.append("\n")
        elif tag == "p":
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = data.replace("\xa0", " ")
        if not text.strip():
            if self._cell is None and self.parts and not self.parts[-1].endswith((" ", "\n")):
                self.parts.append(" ")
            return
        self._emit(text.strip() if self._cell is not None else " ".join(text.split()))
        if self._cell is None:
            self.parts.append(" ")


def storage_to_text(storage: str) -> str:
    """Convert Confluence storage-format XHTML into structured plain text."""
    if not storage:
        return ""
    parser = _StorageParser()
    parser.feed(unescape(storage))
    parser.close()
    text = "".join(parser.parts)
    lines = [re.sub(r"[ \t]+", " ", line).rstrip() for line in text.splitlines()]
    return _BLANKS.sub("\n\n", "\n".join(line for line in lines)).strip()
