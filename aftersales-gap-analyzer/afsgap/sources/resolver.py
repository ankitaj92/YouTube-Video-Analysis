"""Resolve a process name (or a YAML path) into an AS-IS process.

The decision this module makes drives the whole run:

* a Confluence page describes the process  -> **gap mode** (AS-IS vs SAP vs industry)
* nothing credible in Confluence           -> **greenfield mode** (design it from
                                              SAP standard + industry practice)

The "nothing found" branch is a finding, not an error, so the provenance record
carries what was searched, which spaces, which candidates were seen and why each
was rejected. A reviewer must be able to challenge "this process is new".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from ..analysis.current_process import load_current_process
from ..config import Settings
from ..filters.tolerance import ToleranceFilter
from ..models import (
    CurrentProcess,
    PageCandidate,
    ProcessProvenance,
    ValidationReport,
)
from ..prompts import CONFLUENCE_EXTRACT_SYSTEM
from .confluence import ConfluenceClient, ConfluencePage, ConfluenceUnavailableError

logger = logging.getLogger(__name__)


@dataclass
class ResolvedProcess:
    mode: str                       # "gap" | "greenfield"
    provenance: ProcessProvenance
    process: CurrentProcess | None = None
    process_name: str = ""
    process_id: str = ""


def slugify(name: str) -> str:
    return "".join(character if character.isalnum() else "_" for character in name.strip().lower()).strip("_")


class ProcessResolver:
    def __init__(
        self,
        settings: Settings,
        client,
        tolerance: ToleranceFilter,
        confluence: ConfluenceClient | None = None,
    ) -> None:
        self.settings = settings
        self.client = client
        self.tolerance = tolerance
        self._confluence = confluence

    # ------------------------------------------------------------------
    def resolve(
        self,
        target: str,
        report: ValidationReport,
        *,
        page_id: str | None = None,
        spaces: list[str] | None = None,
        force_new: bool = False,
        require_existing: bool = False,
    ) -> ResolvedProcess:
        path = Path(target)
        if path.suffix.lower() in {".yaml", ".yml"} and path.exists():
            return self._from_yaml(path, report)
        return self._from_confluence(
            target, report, page_id=page_id, spaces=spaces,
            force_new=force_new, require_existing=require_existing,
        )

    # ------------------------------------------------------------------
    def _from_yaml(self, path: Path, report: ValidationReport) -> ResolvedProcess:
        process = load_current_process(path, self.tolerance, report)
        self.tolerance.allow_process_name(process.process_name)
        provenance = ProcessProvenance(source="yaml", reference=str(path), searched_for=process.process_name)
        return ResolvedProcess(
            mode="gap", provenance=provenance, process=process,
            process_name=process.process_name, process_id=process.process_id,
        )

    def _from_confluence(
        self,
        process_name: str,
        report: ValidationReport,
        *,
        page_id: str | None,
        spaces: list[str] | None,
        force_new: bool,
        require_existing: bool,
    ) -> ResolvedProcess:
        spaces = spaces if spaces is not None else self.settings.confluence_spaces
        provenance = ProcessProvenance(
            source="confluence", searched_for=process_name, spaces_searched=spaces,
        )

        if force_new:
            provenance.source = "none"
            provenance.not_found_reason = "Greenfield design requested explicitly (--new); Confluence was not searched."
            report.warn("[source] greenfield mode forced; no Confluence lookup was performed.")
            return self._greenfield(process_name, provenance)

        try:
            confluence = self._client()
        except ConfluenceUnavailableError as exc:
            if require_existing:
                raise
            provenance.source = "none"
            provenance.not_found_reason = f"Confluence could not be reached: {exc}"
            report.warn(f"[source] {provenance.not_found_reason}")
            return self._greenfield(process_name, provenance)

        page: ConfluencePage | None = None
        if page_id:
            page = confluence.get_page(page_id)
            provenance.match_score = 1.0
        else:
            candidates = confluence.search(process_name, spaces, self.settings.confluence_search_limit)
            provenance.candidates = [
                PageCandidate(title=c.title, url=c.url, space_key=c.space_key, score=c.score, page_id=c.page_id)
                for c in candidates
            ]
            best = candidates[0] if candidates else None
            if best and best.score >= self.settings.confluence_min_match_score:
                page = confluence.get_page(best.page_id)
                provenance.match_score = best.score
            else:
                reason = (
                    f"No Confluence page matched '{process_name}' above the match threshold "
                    f"({self.settings.confluence_min_match_score}). "
                    + (
                        f"Best candidate was '{best.title}' at {best.score}."
                        if best
                        else "The search returned no pages at all."
                    )
                )
                if require_existing:
                    raise LookupError(reason)
                provenance.source = "none"
                provenance.not_found_reason = reason
                report.warn(f"[source] {reason} Switching to greenfield design mode.")
                return self._greenfield(process_name, provenance)

        provenance.reference = page.citation()
        provenance.url = page.url
        provenance.page_id = page.page_id
        provenance.space_key = page.space_key
        provenance.version = page.version
        provenance.last_modified = page.last_modified
        provenance.last_modified_by = page.last_modified_by

        if not page.text.strip():
            reason = f"Confluence page '{page.title}' was found but has no readable body content."
            if require_existing:
                raise LookupError(reason)
            provenance.not_found_reason = reason
            report.warn(f"[source] {reason} Switching to greenfield design mode.")
            return self._greenfield(process_name, provenance)

        process = self._extract(process_name, page, report)
        return ResolvedProcess(
            mode="gap", provenance=provenance, process=process,
            process_name=process.process_name or process_name,
            process_id=process.process_id or slugify(process_name),
        )

    # ------------------------------------------------------------------
    def _extract(self, process_name: str, page: ConfluencePage, report: ValidationReport) -> CurrentProcess:
        """Turn free-form page content into a structured AS-IS process.

        The page text is tolerance-filtered *before* it reaches the model, so an
        excluded topic documented in Confluence never enters a prompt.
        """
        text = self.tolerance.scrub_input(page.text, "current_process.confluence", report)
        prompt = (
            f"Requested process: {process_name}\n"
            f"Confluence page: {page.title} (space {page.space_key or 'unknown'}, "
            f"version {page.version or 'unknown'}, url {page.url})\n\n"
            "----- PAGE CONTENT -----\n"
            f"{text}\n"
            "----- END PAGE CONTENT -----\n\n"
            "Extract the AS-IS process exactly as this page documents it. Do not improve it, do "
            "not add steps the page does not mention, and do not import knowledge about how such "
            "processes usually work. Where the page is silent, leave the field empty. Record "
            "problems the page describes as pain points. Record custom objects (Z-programs, "
            f"custom tables, enhancements) it names. Use process_id '{slugify(process_name)}'."
        )
        process = self.client.extract(
            system=CONFLUENCE_EXTRACT_SYSTEM, prompt=prompt, schema=CurrentProcess
        )
        cleaned = self.tolerance.validate_output(
            process.model_dump(), "current_process.confluence.output", report
        )
        process = CurrentProcess.model_validate(cleaned)
        if not process.process_id:
            process.process_id = slugify(process_name)
        if not process.steps:
            report.warn(
                f"[source] the Confluence page '{page.title}' yielded no process steps; the gap "
                "analysis will be shallow. Check that the page documents the process itself."
            )
        return process

    def _greenfield(self, process_name: str, provenance: ProcessProvenance) -> ResolvedProcess:
        return ResolvedProcess(
            mode="greenfield", provenance=provenance, process=None,
            process_name=process_name, process_id=slugify(process_name),
        )

    def _client(self) -> ConfluenceClient:
        if self._confluence is None:
            self._confluence = ConfluenceClient(self.settings)
        return self._confluence
