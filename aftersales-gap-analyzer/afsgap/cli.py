"""Command line entry point.

    python -m afsgap run "Defective Parts Return"              # reads Confluence
    python -m afsgap run data/processes/defective_parts_return.yaml
    python -m afsgap run "Battery Pack Return" --new           # design it from scratch
    python -m afsgap confluence-search "Defective Parts Return"
    python -m afsgap list-processes
    python -m afsgap check-filters "text to test"
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import PROJECT_ROOT, load_settings
from .filters.kpi import KpiFilter
from .filters.tolerance import ToleranceFilter
from .pipeline import Pipeline

DEFAULT_FIXTURES = str(PROJECT_ROOT / "tests" / "fixtures")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="afsgap", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="analyse or design a process and write the document")
    run.add_argument("target", help="process name (looked up in Confluence) or a process YAML path")
    run.add_argument("--page-id", help="use this Confluence page instead of searching")
    run.add_argument("--space", action="append", dest="spaces", metavar="KEY",
                     help="restrict the Confluence search to this space (repeatable)")
    run.add_argument("--new", action="store_true",
                     help="skip the Confluence lookup and design the process from scratch")
    run.add_argument("--require-existing", action="store_true",
                     help="fail instead of falling back to greenfield design when nothing is found")
    run.add_argument("--offline", action="store_true", help="use fixtures instead of live research")
    run.add_argument("--fixtures", default=DEFAULT_FIXTURES, help="fixture directory for --offline")
    run.add_argument("--no-cache", action="store_true", help="ignore cached research stages")
    run.add_argument("--skip-industry", action="store_true", help="SAP research and analysis only")

    search = sub.add_parser("confluence-search", help="show which Confluence pages match a process name")
    search.add_argument("name", help="process name to look for")
    search.add_argument("--space", action="append", dest="spaces", metavar="KEY")
    search.add_argument("--offline", action="store_true", help="search the fixture pages")
    search.add_argument("--fixtures", default=DEFAULT_FIXTURES)

    sub.add_parser("list-processes", help="list the local process definitions in data/processes")

    check = sub.add_parser("check-filters", help="show what the exclusion filters do to a piece of text")
    check.add_argument("text", help="text to test")
    return parser


def _confluence_client(args, settings):
    if getattr(args, "offline", False):
        from .sources.offline import OfflineConfluenceClient

        return OfflineConfluenceClient(Path(args.fixtures))
    return None  # the resolver constructs the live client lazily


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-7s %(name)s: %(message)s",
    )

    if args.command == "list-processes":
        for path in sorted((PROJECT_ROOT / "data" / "processes").glob("*.yaml")):
            print(f"  {path.relative_to(PROJECT_ROOT)}")
        return 0

    if args.command == "check-filters":
        tolerance, kpi = ToleranceFilter(), KpiFilter()
        t_result, k_result = tolerance.scrub(args.text), kpi.scrub(args.text)
        print("tolerance -> kept:", t_result.text)
        print("tolerance -> removed:", t_result.removed)
        print("kpi       -> kept:", k_result.text)
        print("kpi       -> removed:", k_result.removed)
        return 0

    settings = load_settings()

    if args.command == "confluence-search":
        from .sources.confluence import ConfluenceClient, ConfluenceUnavailableError

        try:
            client = _confluence_client(args, settings) or ConfluenceClient(settings)
        except ConfluenceUnavailableError as exc:
            print(f"Confluence is not available: {exc}", file=sys.stderr)
            return 3
        pages = client.search(args.name, args.spaces, settings.confluence_search_limit)
        if not pages:
            print(f"No pages matched '{args.name}'. A run would design this process from scratch.")
            return 0
        print(f"{'score':>6}  {'space':<8} title")
        for page in pages:
            marker = "*" if page.score >= settings.confluence_min_match_score else " "
            print(f"{page.score:>6}{marker} {page.space_key:<8} {page.title}")
            print(f"         {page.url}")
        print(f"\n* = at or above the match threshold ({settings.confluence_min_match_score}); "
              "the top starred page would be used.")
        return 0

    if args.offline:
        from .llm.offline import OfflineClient, seed_page_cache

        client = OfflineClient(Path(args.fixtures))
        seed_page_cache(Path(args.fixtures), settings.cache_dir)
    else:
        from .llm import ClaudeClient

        client = ClaudeClient(settings)

    pipeline = Pipeline(settings, client, offline=args.offline, use_cache=not args.no_cache)
    try:
        result, paths = pipeline.run(
            args.target,
            skip_industry=args.skip_industry,
            page_id=args.page_id,
            spaces=args.spaces,
            force_new=args.new,
            require_existing=args.require_existing,
            confluence=_confluence_client(args, settings),
        )
    except LookupError as exc:
        print(f"Nothing to analyse: {exc}", file=sys.stderr)
        print("Re-run without --require-existing to design the process from scratch.", file=sys.stderr)
        return 3

    print()
    print(f"Mode         : {'greenfield design' if result.mode == 'greenfield' else 'gap analysis'}")
    print(f"Source       : {result.provenance.reference or result.provenance.not_found_reason or 'n/a'}")
    if result.mode == "greenfield":
        print(f"Decisions    : {len(result.blueprint.design_decisions)}")
        print(f"Design steps : {len(result.blueprint.recommended_flow)}")
    else:
        print(f"Verdict      : {result.gap_analysis.verdict}")
        print(f"Gaps found   : {len(result.gap_analysis.gaps)}")
    print(f"T-codes      : {len(result.sap_research.verified_tcodes)} verified, "
          f"{len(result.sap_research.dropped_tcodes)} rejected")
    print(f"Exclusions   : {len(result.validation.exclusions)}")
    print(f"Validation   : {'PASSED' if result.validation.passed else 'FAILED'}")
    for path in paths.values():
        print(f"Written      : {path}")
    for error in result.validation.errors:
        print(f"  ERROR   {error}", file=sys.stderr)
    for warning in result.validation.warnings:
        print(f"  WARNING {warning}", file=sys.stderr)

    if not result.validation.passed and settings.fail_on_validation_error:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
