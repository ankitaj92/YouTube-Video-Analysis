"""Design document rendering.

Two document shapes share most of their sections:

* **gap** - an AS-IS exists, so the document compares three views and concludes
  with a verdict on the current process.
* **greenfield** - no AS-IS was found, so the document proposes how to implement
  the process, anchored in the same SAP and industry evidence.

Sections 1 and the middle differ; the SAP reference, the industry benchmark, the
evidence register and the exclusion log are identical in both, and rendered by
the same code.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..models import (
    GapAnalysis,
    IndustryResearchResult,
    ProcessBlueprint,
    ProcessProvenance,
    RunResult,
    SapResearchResult,
    ValidationReport,
)

logger = logging.getLogger(__name__)

VERDICT_LABEL = {
    "best_in_class": "BEST IN CLASS - the current process matches or exceeds SAP standard and leading practice",
    "at_par": "AT PAR - sound, but with no advantage over SAP standard or leading practice",
    "improvement_needed": "IMPROVEMENT NEEDED - material gaps against SAP standard and/or leading practice",
    "insufficient_evidence": "INSUFFICIENT EVIDENCE - research did not return enough grounded material to judge",
}

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}

META_OPEN = "<!-- afsgap:meta -->"
META_CLOSE = "<!-- /afsgap:meta -->"

EXCLUSION_NOTE = (
    "> **Scope exclusions applied to this document:** tolerance topics are excluded "
    "end to end (input, research, analysis and output). Industry benchmark content is "
    "qualitative and carries no KPIs or metrics. Transaction codes appear only where a "
    "public SAP source literally contains them."
)


def _table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return "_None._\n"
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join([" --- "] * len(headers)) + "|"]
    for row in rows:
        cells = [(cell or "").replace("\n", " ").replace("|", "\\|") for cell in row]
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out) + "\n"


def _bullets(items: list[str]) -> list[str]:
    return [f"- {item}" for item in items] + [""]


# ---------------------------------------------------------------------------
# Shared sections
# ---------------------------------------------------------------------------
def _header(result: RunResult, subtitle: str) -> list[str]:
    process = result.current_process
    lines = [
        f"# {subtitle} - {result.process_name}",
        "",
        f"**Process ID:** {result.process_id}  ",
        f"**Business area:** {process.business_area if process else 'Automotive aftersales logistics'}  ",
        f"**Transition:** {process.source_system if process else 'SAP ECC'} to "
        f"{process.target_system if process else 'SAP S/4HANA'}  ",
        f"**Run date:** {result.run_date.isoformat()}  ",
        f"**Mode:** {'greenfield design (no existing process found)' if result.mode == 'greenfield' else 'gap analysis'}"
        f"{' - offline fixtures' if result.offline else ''}",
        "",
        META_OPEN,
        EXCLUSION_NOTE,
        META_CLOSE,
        "",
    ]
    return lines


def _section_provenance(number: int, provenance: ProcessProvenance) -> list[str]:
    lines = [f"## {number}. Process source", ""]
    if provenance.source == "confluence" and provenance.url:
        lines += [
            f"The AS-IS description was read from Confluence: **{provenance.reference}**",
            "",
            f"- Page: {provenance.url}",
            f"- Space: {provenance.space_key or 'n/a'} | Version: {provenance.version or 'n/a'} | "
            f"Last modified: {provenance.last_modified[:10] or 'unknown'}"
            + (f" by {provenance.last_modified_by}" if provenance.last_modified_by else ""),
            f"- Matched on: '{provenance.searched_for}' (title match score {provenance.match_score})",
            "",
            "_The process record below is a transcription of that page. Where the page is silent, "
            "the record is empty - absences are findings, not omissions._",
            "",
        ]
    elif provenance.source == "yaml":
        lines += [f"The AS-IS description was read from the local process definition `{provenance.reference}`.", ""]
    else:
        lines += [
            "**No existing process documentation was found.** This document therefore proposes an "
            "implementation rather than analysing gaps.",
            "",
            f"- Searched for: '{provenance.searched_for}'",
            f"- Spaces searched: {', '.join(provenance.spaces_searched) if provenance.spaces_searched else 'all accessible spaces'}",
            f"- Outcome: {provenance.not_found_reason or 'no match'}",
            "",
        ]
    if provenance.candidates:
        lines += ["**Candidate pages considered**", ""]
        lines.append(
            _table(
                ["Title", "Space", "Match score", "Page"],
                [[c.title, c.space_key, str(c.score), c.url] for c in provenance.candidates],
            )
        )
        lines += [
            "_If the right page is in this list but was not selected, re-run with `--page-id` to "
            "force it, or lower the match threshold._",
            "",
        ]
    return lines


def _section_sap(number: int, sap: SapResearchResult) -> list[str]:
    lines = [f"## {number}. SAP standard reference", ""]
    lines += [
        META_OPEN,
        "_Sourced exclusively from official public SAP domains. Area names such as SAP SD, "
        "SAP EWM, SAP CMH, master data, the dealer front-end/Sales Order Plus and aftersales "
        "were used as search guidance only; only findings actually returned by a public SAP "
        "source appear below._",
        META_CLOSE,
        "",
    ]
    if sap.standard.summary:
        lines += [sap.standard.summary, ""]
    lines += [f"### {number}.1 Standard process flow", ""]
    lines.append(
        _table(
            ["#", "Step", "SAP area", "Description", "Source"],
            [
                [str(s.seq), s.name, s.sap_area, s.description, ", ".join(e.url for e in s.evidence)]
                for s in sap.standard.standard_process_flow
            ],
        )
    )
    lines += [f"### {number}.2 Relevant SAP modules and components", ""]
    lines.append(
        _table(
            ["Module / component", "Role in this process", "Source"],
            [[m.name, m.role_in_process, ", ".join(e.url for e in m.evidence)] for m in sap.standard.modules],
        )
    )
    lines += [f"### {number}.3 Transaction codes (verified)", ""]
    lines += [
        "_Each code below was found literally on the cited public SAP page. Codes that could not "
        "be verified are listed underneath with the reason, and are not used anywhere else in "
        "this document._",
        "",
    ]
    lines.append(
        _table(
            ["T-code", "Purpose", "SAP area", "Evidence"],
            [[c.tcode, c.purpose, c.sap_area, c.source_url] for c in sap.verified_tcodes],
        )
    )
    if sap.dropped_tcodes:
        lines += ["**Rejected transaction codes**", ""]
        lines.append(
            _table(["Candidate", "Rejected because", "Cited source"],
                   [[d.tcode, d.reason, d.source_url] for d in sap.dropped_tcodes])
        )
    if sap.standard.master_data_objects:
        lines += [f"### {number}.4 Master data objects", ""] + _bullets(sap.standard.master_data_objects)
    if sap.standard.integration_points:
        lines += [f"### {number}.5 Integration points", ""] + _bullets(sap.standard.integration_points)
    if sap.standard.s4_specific_changes:
        lines += [f"### {number}.6 What changes in S/4HANA versus ECC", ""] + _bullets(sap.standard.s4_specific_changes)
    if sap.standard.open_questions:
        lines += [f"### {number}.7 Open questions for SAP / the functional team", ""] + _bullets(
            sap.standard.open_questions
        )
    return lines


def _section_industry(number: int, industry: IndustryResearchResult) -> list[str]:
    benchmark = industry.benchmark
    lines = [f"## {number}. Industry practice benchmark", ""]
    lines += [
        META_OPEN,
        "_Qualitative only. Sources were found through several broad search framings and then "
        "filtered against a credible-source allowlist (consultancies, standards and industry "
        "bodies, research institutes, recognised industry press), supplemented by peer-reviewed "
        "work retrieved from OpenAlex._",
        META_CLOSE,
        "",
    ]
    if benchmark.summary:
        lines += [benchmark.summary, ""]
    lines += [f"### {number}.1 Leading practices", ""]
    lines.append(
        _table(
            ["Practice", "Maturity", "Description", "Source"],
            [
                [p.title, p.maturity, p.description, ", ".join(e.url for e in p.evidence)]
                for p in benchmark.leading_practices
            ],
        )
    )
    for index, (heading, items) in enumerate(
        (
            ("Common operating models", benchmark.common_operating_models),
            ("Automation and digital enablers", benchmark.automation_and_digital_enablers),
            ("Recurring risks and failure modes", benchmark.risks_and_failure_modes),
        ),
        start=2,
    ):
        if items:
            lines += [f"### {number}.{index} {heading}", ""] + _bullets(items)
    if benchmark.scholarly_works:
        lines += [f"### {number}.5 Peer-reviewed grounding (OpenAlex)", ""]
        lines.append(
            _table(
                ["Title", "Year", "Venue", "DOI / URL"],
                [[w.title, str(w.year or ""), w.venue, w.doi or w.url] for w in benchmark.scholarly_works],
            )
        )
    return lines


def _section_evidence(number: int, sap: SapResearchResult, industry: IndustryResearchResult) -> list[str]:
    lines = [f"## {number}. Evidence register", "", f"### {number}.1 SAP sources used", ""]
    lines.append(_table(["Domain", "Title", "URL"], [[s.domain, s.title, s.url] for s in sap.sources]))
    lines += [f"### {number}.2 Industry sources retained", ""]
    lines.append(
        _table(["Category", "Domain", "Title", "URL"],
               [[s.category, s.domain, s.title, s.url] for s in industry.sources])
    )
    if industry.rejected_sources:
        lines += [f"### {number}.3 Sources rejected by the credibility filter", ""]
        lines.append(_table(["Domain", "Tier", "URL"], [[s.domain, s.tier, s.url] for s in industry.rejected_sources]))
    lines += [f"### {number}.4 Queries run", ""]
    lines += [f"- [SAP] {query}" for query in sap.queries_used]
    lines += [f"- [Industry] {query}" for query in industry.queries_used]
    lines += [""]
    return lines


def _section_validation(number: int, validation: ValidationReport) -> list[str]:
    lines = [f"## {number}. Exclusion and validation log", "",
             f"**Validation status:** {'PASSED' if validation.passed else 'FAILED'}", ""]
    if validation.errors:
        lines += ["**Errors**", ""] + _bullets(validation.errors)
    if validation.warnings:
        lines += ["**Warnings**", ""] + _bullets(validation.warnings)
    lines += ["**Content excluded by rule**", ""]
    lines.append(
        _table(
            ["Rule", "Stage", "Location", "Excluded content", "Matched"],
            [
                [e.rule, e.stage, e.location,
                 e.excluded_text[:160] + ("..." if len(e.excluded_text) > 160 else ""),
                 ", ".join(e.matched_terms)]
                for e in validation.exclusions
            ],
        )
    )
    lines += [
        "",
        "---",
        "",
        "_Generated by afsgap. Every SAP statement is traceable to a public SAP source; every "
        "industry statement to a credible publisher or peer-reviewed work. Unverifiable claims "
        "were dropped rather than softened._",
    ]
    return lines


# ---------------------------------------------------------------------------
# Gap document
# ---------------------------------------------------------------------------
def _render_gap(result: RunResult) -> str:
    process = result.current_process
    analysis: GapAnalysis = result.gap_analysis
    lines = _header(result, "Process Design & Gap Analysis")

    lines += ["## 1. Verdict", "", f"**{VERDICT_LABEL.get(analysis.verdict, analysis.verdict)}**", "",
              analysis.verdict_rationale, "", "### Executive summary", "", analysis.executive_summary, ""]

    lines += _section_provenance(2, result.provenance)

    lines += ["## 3. Current (AS-IS) process", ""]
    if process:
        if process.description:
            lines += [process.description, ""]
        if process.scope_in or process.scope_out:
            lines += [f"**In scope:** {', '.join(process.scope_in) or '-'}  ",
                      f"**Out of scope:** {', '.join(process.scope_out) or '-'}", ""]
        lines.append(
            _table(
                ["#", "Step", "Actor", "System", "Description", "Pain points"],
                [[str(s.seq), s.name, s.actor, s.system, s.description, "; ".join(s.pain_points)]
                 for s in process.steps],
            )
        )
        if process.known_pain_points:
            lines += ["**Known pain points**", ""] + _bullets(process.known_pain_points)
        if process.custom_objects:
            lines += ["**Custom objects in ECC today**", ""] + _bullets(process.custom_objects)

    lines += _section_sap(4, result.sap_research)
    lines += _section_industry(5, result.industry_research)

    lines += ["## 6. Gap analysis", ""]
    gaps = sorted(analysis.gaps, key=lambda g: SEVERITY_ORDER.get(g.severity, 9))
    lines.append(
        _table(
            ["ID", "Dimension", "Gap", "Severity", "S/4HANA disposition", "Effort"],
            [[g.gap_id, g.dimension, g.title, g.severity, g.s4_disposition, g.effort] for g in gaps],
        )
    )
    for gap in gaps:
        lines += [
            f"### {gap.gap_id} - {gap.title}", "",
            f"**Dimension:** {gap.dimension} | **Severity:** {gap.severity} | "
            f"**Disposition:** {gap.s4_disposition} | **Effort:** {gap.effort}", "",
            f"**Today:** {gap.current_state}", "",
            f"**SAP standard:** {gap.sap_standard_reference}", "",
        ]
        if gap.industry_reference:
            lines += [f"**Industry practice:** {gap.industry_reference}", ""]
        lines += [f"**Gap:** {gap.gap_description}", "", f"**Recommendation:** {gap.recommendation}", ""]
        if gap.dependencies:
            lines += [f"**Dependencies:** {', '.join(gap.dependencies)}", ""]
        if gap.evidence:
            lines += ["**Evidence**", ""] + [f'- "{e.quote}" - {e.url}' for e in gap.evidence] + [""]

    lines += ["## 7. TO-BE process design", ""]
    lines.append(
        _table(
            ["#", "Step", "Actor", "System", "Description", "Change vs today"],
            [[str(s.seq), s.name, s.actor, s.system, s.description, s.change_vs_current]
             for s in analysis.to_be_process],
        )
    )

    lines += ["## 8. Recommendations and roadmap", ""]
    if analysis.quick_wins:
        lines += ["### Quick wins", ""] + _bullets(analysis.quick_wins)
    if analysis.roadmap_phases:
        lines += ["### Phasing", ""] + [f"{i}. {item}" for i, item in enumerate(analysis.roadmap_phases, 1)] + [""]
    if analysis.risks:
        lines += ["### Risks", ""] + _bullets(analysis.risks)
    if analysis.assumptions:
        lines += ["### Assumptions", ""] + _bullets(analysis.assumptions)

    lines += _section_evidence(9, result.sap_research, result.industry_research)
    lines += _section_validation(10, result.validation)
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Greenfield document
# ---------------------------------------------------------------------------
def _render_blueprint(result: RunResult) -> str:
    blueprint: ProcessBlueprint = result.blueprint
    lines = _header(result, "Process Implementation Blueprint")

    lines += ["## 1. Recommendation summary", "",
              "**NEW PROCESS - no existing implementation was found, so this document proposes one.**",
              "", blueprint.summary, ""]
    if blueprint.why_new:
        lines += [f"**Why this is being designed from scratch:** {blueprint.why_new}", ""]
    if blueprint.design_principles:
        lines += ["### Design principles", ""] + _bullets(blueprint.design_principles)

    lines += _section_provenance(2, result.provenance)
    lines += _section_sap(3, result.sap_research)
    lines += _section_industry(4, result.industry_research)

    lines += ["## 5. Recommended process design", ""]
    lines.append(
        _table(
            ["#", "Step", "Actor", "System", "Description", "Note"],
            [[str(s.seq), s.name, s.actor, s.system, s.description, s.change_vs_current]
             for s in blueprint.recommended_flow],
        )
    )

    lines += ["## 6. Design decisions", "",
              "_Each decision states its alternatives and what it rests on, so it can be "
              "challenged individually without unpicking the design._", ""]
    for index, decision in enumerate(blueprint.design_decisions, start=1):
        lines += [f"### D{index:02d} - {decision.decision}", ""]
        if decision.options_considered:
            lines += ["**Options considered**", ""] + _bullets(decision.options_considered)
        lines += [f"**Recommendation:** {decision.recommendation}", "",
                  f"**Rationale:** {decision.rationale}", ""]
        lines += [f"**SAP basis:** {decision.sap_basis or '_not established from the retrieved SAP sources_'}", ""]
        if decision.industry_basis:
            lines += [f"**Industry basis:** {decision.industry_basis}", ""]
        if decision.evidence:
            lines += ["**Evidence**", ""] + [f'- "{e.quote}" - {e.url}' for e in decision.evidence] + [""]

    lines += ["## 7. Implementation prerequisites", ""]
    if blueprint.configuration_scope:
        lines += ["### 7.1 Configuration scope", ""] + _bullets(blueprint.configuration_scope)
    if blueprint.master_data_prerequisites:
        lines += ["### 7.2 Master data prerequisites", ""] + _bullets(blueprint.master_data_prerequisites)
    if blueprint.integration_points:
        lines += ["### 7.3 Integration points", ""] + _bullets(blueprint.integration_points)
    if blueprint.roles_and_responsibilities:
        lines += ["### 7.4 Roles and responsibilities", ""]
        lines.append(
            _table(["Role", "Responsibilities"],
                   [[r.role, "; ".join(r.responsibilities)] for r in blueprint.roles_and_responsibilities])
        )

    lines += ["## 8. Delivery, risks and open questions", ""]
    if blueprint.roadmap_phases:
        lines += ["### Phasing", ""] + [f"{i}. {item}" for i, item in enumerate(blueprint.roadmap_phases, 1)] + [""]
    if blueprint.risks:
        lines += ["### Risks", ""] + _bullets(blueprint.risks)
    if blueprint.open_questions:
        lines += ["### Open questions", ""] + _bullets(blueprint.open_questions)
    if blueprint.assumptions:
        lines += ["### Assumptions", ""] + _bullets(blueprint.assumptions)

    lines += _section_evidence(9, result.sap_research, result.industry_research)
    lines += _section_validation(10, result.validation)
    return "\n".join(lines) + "\n"


def render_markdown(result: RunResult) -> str:
    if result.mode == "greenfield":
        if result.blueprint is None:
            raise ValueError("greenfield run has no blueprint to render")
        return _render_blueprint(result)
    if result.gap_analysis is None:
        raise ValueError("gap run has no gap analysis to render")
    return _render_gap(result)


# ---------------------------------------------------------------------------
# Output files
# ---------------------------------------------------------------------------
def write_reports(result: RunResult, output_dir: Path, base_name: str | None = None) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = "blueprint" if result.mode == "greenfield" else "design_document"
    stem = base_name or f"{result.process_id}_{result.run_date.isoformat()}"
    written: dict[str, Path] = {}

    markdown_path = output_dir / f"{stem}_{suffix}.md"
    markdown_path.write_text(render_markdown(result), encoding="utf-8")
    written["markdown"] = markdown_path

    json_path = output_dir / f"{stem}_run.json"
    json_path.write_text(json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False), encoding="utf-8")
    written["json"] = json_path

    docx_path = _write_docx(result, output_dir / f"{stem}_{suffix}.docx")
    if docx_path:
        written["docx"] = docx_path
    return written


def _write_docx(result: RunResult, path: Path) -> Path | None:
    try:
        from docx import Document
    except ImportError:
        logger.info("python-docx not installed - skipping DOCX export.")
        return None

    document = Document()
    for line in render_markdown(result).splitlines():
        stripped = line.strip()
        if stripped.startswith("<!--"):
            continue
        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            document.add_heading(stripped.lstrip("# ").strip(), level=min(level, 4))
        elif stripped.startswith("- "):
            document.add_paragraph(stripped[2:], style="List Bullet")
        elif stripped:
            document.add_paragraph(stripped)
    document.save(path)
    return path
