"""Runtime configuration.

Everything tunable lives here or in ``afsgap/resources/*.yaml`` so that a
process analyst can change behaviour without touching pipeline code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:  # optional, only used for local development convenience
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - dotenv is optional
    pass

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent
RESOURCE_DIR = PACKAGE_ROOT / "resources"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


@dataclass
class Settings:
    """Single source of truth for models, limits and paths."""

    # --- model -------------------------------------------------------------
    model: str = field(default_factory=lambda: os.getenv("AFSGAP_MODEL", "claude-opus-5"))
    effort: str = field(default_factory=lambda: os.getenv("AFSGAP_EFFORT", "high"))
    research_max_tokens: int = field(default_factory=lambda: _env_int("AFSGAP_RESEARCH_MAX_TOKENS", 32000))
    extract_max_tokens: int = field(default_factory=lambda: _env_int("AFSGAP_EXTRACT_MAX_TOKENS", 16000))

    # Anthropic server-side refusal fallbacks. Harmless for this domain but
    # enabled by default so a classifier refusal never kills a weekend run.
    enable_server_side_fallbacks: bool = field(
        default_factory=lambda: _env_bool("AFSGAP_SERVER_SIDE_FALLBACKS", True)
    )
    fallback_beta: str = "server-side-fallback-2026-07-01"

    # Web search server tool. The dated variant supports dynamic filtering on
    # Opus 5 / Sonnet 5; override for older models.
    web_search_tool_type: str = field(
        default_factory=lambda: os.getenv("AFSGAP_WEB_SEARCH_TOOL", "web_search_20260209")
    )
    web_fetch_tool_type: str = field(
        default_factory=lambda: os.getenv("AFSGAP_WEB_FETCH_TOOL", "web_fetch_20260209")
    )
    sap_max_searches: int = field(default_factory=lambda: _env_int("AFSGAP_SAP_MAX_SEARCHES", 12))
    industry_max_searches: int = field(default_factory=lambda: _env_int("AFSGAP_INDUSTRY_MAX_SEARCHES", 14))

    # --- research ----------------------------------------------------------
    openalex_mailto: str = field(default_factory=lambda: os.getenv("AFSGAP_OPENALEX_MAILTO", ""))
    openalex_per_page: int = field(default_factory=lambda: _env_int("AFSGAP_OPENALEX_PER_PAGE", 10))
    openalex_from_year: int = field(default_factory=lambda: _env_int("AFSGAP_OPENALEX_FROM_YEAR", 2015))
    http_timeout: int = field(default_factory=lambda: _env_int("AFSGAP_HTTP_TIMEOUT", 30))

    # --- validation --------------------------------------------------------
    # Hard gate: abort the run if a forbidden topic survives every filter.
    fail_on_validation_error: bool = field(
        default_factory=lambda: _env_bool("AFSGAP_FAIL_ON_VALIDATION_ERROR", True)
    )
    # A T-code is only reported when a public SAP page literally contains it.
    require_literal_tcode_evidence: bool = field(
        default_factory=lambda: _env_bool("AFSGAP_REQUIRE_LITERAL_TCODE_EVIDENCE", True)
    )

    # --- paths -------------------------------------------------------------
    cache_dir: Path = field(default_factory=lambda: Path(os.getenv("AFSGAP_CACHE_DIR", PROJECT_ROOT / ".cache")))
    output_dir: Path = field(default_factory=lambda: Path(os.getenv("AFSGAP_OUTPUT_DIR", PROJECT_ROOT / "output")))
    resource_dir: Path = RESOURCE_DIR

    def ensure_dirs(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings
