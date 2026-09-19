"""Prepare a Pydantic JSON schema for Ollama's ``format`` parameter.

Ollama turns the schema into a grammar that constrains decoding. Pydantic emits
``$ref``/``$defs``, which is legal JSON Schema but is handled unevenly across
Ollama and llama.cpp versions - so references are inlined here and the
keywords that carry no decoding meaning are dropped, leaving a plain,
self-contained schema.
"""

from __future__ import annotations

from typing import Any

DROPPED_KEYWORDS = {"title", "default", "examples", "$schema", "$comment", "readOnly", "writeOnly"}


def _resolve(node: Any, defs: dict[str, Any], seen: frozenset[str]) -> Any:
    if isinstance(node, list):
        return [_resolve(item, defs, seen) for item in node]
    if not isinstance(node, dict):
        return node

    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        name = ref.split("/")[-1]
        if name in seen:
            # Self-referential model: the grammar cannot expand it forever, so
            # degrade to a free-form object rather than recursing.
            return {"type": "object"}
        target = defs.get(name)
        if target is None:
            return {"type": "object"}
        resolved = _resolve(target, defs, seen | {name})
        # keep any sibling keywords that sat alongside the $ref
        extra = {k: _resolve(v, defs, seen) for k, v in node.items() if k != "$ref" and k not in DROPPED_KEYWORDS}
        return {**resolved, **extra} if extra else resolved

    return {
        key: _resolve(value, defs, seen)
        for key, value in node.items()
        if key not in DROPPED_KEYWORDS and key != "$defs"
    }


def flatten_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Inline ``$defs`` and strip decoration, returning a standalone schema."""
    defs = schema.get("$defs", {}) or {}
    return _resolve({k: v for k, v in schema.items() if k != "$defs"}, defs, frozenset())
