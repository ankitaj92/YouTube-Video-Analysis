"""Loads the editable YAML knowledge files that drive filtering."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .config import RESOURCE_DIR


@lru_cache(maxsize=None)
def load_resource(name: str, resource_dir: str | None = None) -> dict[str, Any]:
    directory = Path(resource_dir) if resource_dir else RESOURCE_DIR
    path = directory / f"{name}.yaml"
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def compile_patterns(patterns: list[str]) -> list[re.Pattern[str]]:
    return [re.compile(p, re.IGNORECASE) for p in patterns]
