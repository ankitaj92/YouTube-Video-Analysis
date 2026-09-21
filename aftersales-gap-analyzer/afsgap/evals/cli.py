"""Eval command line.

    python -m afsgap.evals run --offline            # the free, deterministic subset
    python -m afsgap.evals run --tags live          # the expensive cases
    python -m afsgap.evals score                    # re-grade existing runs, no cost
    python -m afsgap.evals compare a.json b.json    # did the change help?
    python -m afsgap.evals cases                    # what is in the eval set
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from ..config import PROJECT_ROOT, load_settings
from .judge import build_grader
from .report import compare, load_run, markdown_report, scoreboard
from .rubric import derive_rubric
from .runner import CASES_DIR, RESULTS_DIR, EvalRunner, load_cases, save


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="afsgap.evals", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run the eval set through the pipeline and score it")
    run.add_argument("--only", nargs="*", help="case ids to run")
    run.add_argument("--tags", nargs="*", help="run cases carrying any of these tags")
    run.add_argument("--offline", action="store_true", help="fixtures only - free and deterministic")
    run.add_argument("--fixtures", default=str(PROJECT_ROOT / "tests" / "fixtures"))
    run.add_argument("--llm", choices=["claude", "ollama"], default=None)
    run.add_argument("--search", default=None)
    run.add_argument("--grader", default="keywords", help="keywords | claude | ollama")
    run.add_argument("--label", default="", help="name for this run, used in the comparison")
    run.add_argument("--no-cache", action="store_true")
    run.add_argument("--markdown", action="store_true", help="also write a markdown report")

    score = sub.add_parser("score", help="grade run records already in output/ - no pipeline, no cost")
    score.add_argument("--only", nargs="*")
    score.add_argument("--tags", nargs="*")
    score.add_argument("--output-dir", default=str(PROJECT_ROOT / "output"))
    score.add_argument("--grader", default="keywords")
    score.add_argument("--label", default="rescore")
    score.add_argument("--markdown", action="store_true")

    comparison = sub.add_parser("compare", help="compare two saved eval runs")
    comparison.add_argument("before")
    comparison.add_argument("after")

    listing = sub.add_parser("cases", help="show the eval set and what each case expects")
    listing.add_argument("--only", nargs="*")
    listing.add_argument("--tags", nargs="*")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)-7s %(name)s: %(message)s")
    for noisy in ("primp", "ddgs", "urllib3", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.DEBUG if args.verbose else logging.WARNING)

    if args.command == "compare":
        print(compare(load_run(Path(args.before)), load_run(Path(args.after))))
        return 0

    cases = load_cases(CASES_DIR, only=(args.only or []) + (getattr(args, "tags", None) or []))
    if not cases:
        print("No cases matched.", file=sys.stderr)
        return 1

    if args.command == "cases":
        _print_cases(cases)
        return 0

    settings = load_settings()

    if args.command == "score":
        runner = EvalRunner(settings, client=None, grader=build_grader(args.grader, settings))
        run_files = sorted(Path(args.output_dir).glob("*_run.json"))
        if not run_files:
            print(f"No run records in {args.output_dir}. Run the pipeline first.", file=sys.stderr)
            return 1
        result = runner.score_existing(cases, run_files, label=args.label)
    else:
        client, confluence = _backends(args, settings)
        runner = EvalRunner(settings, client, grader=build_grader(args.grader, settings),
                            confluence=confluence, offline=args.offline)
        result = runner.execute(cases, label=args.label or ("offline" if args.offline else "run"),
                                use_cache=not args.no_cache)

    print()
    print(scoreboard(result))
    path = save(result, RESULTS_DIR)
    print()
    print(f"Saved: {path}")
    if getattr(args, "markdown", False):
        md = path.with_suffix(".md")
        md.write_text(markdown_report(result), encoding="utf-8")
        print(f"Saved: {md}")

    total = result.aggregate()
    if total.blocking_failures:
        print(f"\n{total.blocking_failures} blocking failure(s).", file=sys.stderr)
        return 2
    return 0


def _backends(args, settings):
    if args.offline:
        from ..llm.offline import OfflineClient, seed_page_cache
        from ..sources.offline import OfflineConfluenceClient

        fixtures = Path(args.fixtures)
        seed_page_cache(fixtures, settings.cache_dir)
        return OfflineClient(fixtures), OfflineConfluenceClient(fixtures)

    backend = (args.llm or settings.llm_backend or "claude").lower()
    if backend == "ollama":
        from ..llm.ollama import OllamaClient
        from ..search.base import build_backend

        client = OllamaClient(settings, build_backend(settings, args.search))
        client.check()
        return client, None
    from ..llm import ClaudeClient

    return ClaudeClient(settings), None


def _print_cases(cases) -> None:
    from ..analysis.current_process import load_current_process
    from ..filters import ToleranceFilter
    from ..models import ValidationReport

    print(f"{len(cases)} case(s) in the eval set")
    print()
    for case in cases:
        derived = 0
        if case.target.endswith((".yaml", ".yml")) and Path(case.target).exists():
            process = load_current_process(Path(case.target), ToleranceFilter(), ValidationReport())
            derived = len(derive_rubric(process))
        print(f"  {case.id}")
        print(f"    {case.description}")
        print(f"    target={case.target}  mode={case.mode}  tags={','.join(case.tags) or '-'}"
              f"{'  offline' if case.offline else ''}")
        print(f"    expectations: {derived} derived from the process definition, "
              f"{len(case.rubric)} authored, {len(case.forbidden)} forbidden statement(s)")
        print()


if __name__ == "__main__":
    raise SystemExit(main())
