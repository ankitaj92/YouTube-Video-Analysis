"""Command line entry point.

    python -m afsgap run data/processes/defective_parts_return.yaml
    python -m afsgap run data/processes/defective_parts_return.yaml --offline
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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="afsgap", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="analyse a process and generate the design document")
    run.add_argument("process", help="path to the process YAML file")
    run.add_argument("--offline", action="store_true", help="use fixtures instead of live research")
    run.add_argument("--fixtures", default=str(PROJECT_ROOT / "tests" / "fixtures"),
                     help="fixture directory for --offline")
    run.add_argument("--no-cache", action="store_true", help="ignore cached research stages")
    run.add_argument("--skip-industry", action="store_true", help="SAP research and gap analysis only")

    sub.add_parser("list-processes", help="list the process definitions found in data/processes")

    check = sub.add_parser("check-filters", help="show what the exclusion filters do to a piece of text")
    check.add_argument("text", help="text to test")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-7s %(name)s: %(message)s",
    )

    if args.command == "list-processes":
        directory = PROJECT_ROOT / "data" / "processes"
        for path in sorted(directory.glob("*.yaml")):
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
    if args.offline:
        from .llm.offline import OfflineClient, seed_page_cache

        client = OfflineClient(Path(args.fixtures))
        seeded = seed_page_cache(Path(args.fixtures), settings.cache_dir)
        logging.getLogger(__name__).info("Offline mode: seeded %d fixture page(s) into the cache.", seeded)
    else:
        from .llm import ClaudeClient

        client = ClaudeClient(settings)

    pipeline = Pipeline(settings, client, offline=args.offline, use_cache=not args.no_cache)
    result, paths = pipeline.run(args.process, skip_industry=args.skip_industry)

    print()
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
