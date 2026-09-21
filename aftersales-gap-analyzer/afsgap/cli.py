"""Command line entry point.

    python -m afsgap run "Defective Parts Return"              # reads Confluence
    python -m afsgap run "Defective Parts Return" --llm ollama # fully local
    python -m afsgap doctor                                    # check the local setup
    python -m afsgap export-ca-bundle                          # corporate TLS proxy fix
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
from .filters.tolerance import ExcludedProcessError, ToleranceFilter
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
    run.add_argument("--llm", choices=["claude", "ollama"], default=None,
                     help="generation backend (default: AFSGAP_LLM, else claude)")
    run.add_argument("--search", default=None,
                     help="search backend or chain, e.g. duckduckgo,mojeek,seeds (default: AFSGAP_SEARCH)")
    run.add_argument("--offline", action="store_true", help="use fixtures instead of live research")
    run.add_argument("--fixtures", default=DEFAULT_FIXTURES, help="fixture directory for --offline")
    run.add_argument("--no-cache", action="store_true", help="ignore cached research stages")
    run.add_argument("--skip-industry", action="store_true", help="SAP research and analysis only")

    search = sub.add_parser("confluence-search", help="show which Confluence pages match a process name")
    search.add_argument("name", help="process name to look for")
    search.add_argument("--space", action="append", dest="spaces", metavar="KEY")
    search.add_argument("--offline", action="store_true", help="search the fixture pages")
    search.add_argument("--fixtures", default=DEFAULT_FIXTURES)

    doctor = sub.add_parser("doctor", help="check that the configured backends are reachable")
    doctor.add_argument("--llm", choices=["claude", "ollama"], default=None)
    doctor.add_argument("--search", default=None,
                        help="search backend or comma-separated chain to test")
    doctor.add_argument("--bench", action="store_true",
                        help="measure local model speed and recommend settings")

    export_ca = sub.add_parser(
        "export-ca-bundle",
        help="write the machine's root certificates to a PEM file (fixes corporate TLS errors)",
    )
    export_ca.add_argument("--out", default=str(PROJECT_ROOT / "corp-ca-bundle.pem"),
                           help="where to write the bundle")

    sub.add_parser("list-processes", help="list the local process definitions in data/processes")

    check = sub.add_parser("check-filters", help="show what the exclusion filters do to a piece of text")
    check.add_argument("text", help="text to test")
    return parser


def _confluence_client(args, settings):
    if getattr(args, "offline", False):
        from .sources.offline import OfflineConfluenceClient

        return OfflineConfluenceClient(Path(args.fixtures))
    return None  # the resolver constructs the live client lazily



def _build_llm(settings, llm: str | None, search: str | None):
    """Construct the generation backend, wiring retrieval for the local one."""
    backend = (llm or settings.llm_backend or "claude").lower()
    if backend == "ollama":
        from .llm.ollama import OllamaClient
        from .search.base import build_backend

        client = OllamaClient(settings, build_backend(settings, search))
        client.check()          # fail fast with a useful message, not mid-run
        return client
    from .llm import ClaudeClient

    return ClaudeClient(settings)


def _bench(settings, client) -> None:
    """Measure the local model and translate the result into settings."""
    from .llm.ollama import OllamaUnavailableError

    print()
    print("Benchmarking the local model (this takes a minute)...")
    try:
        result = client.benchmark()
    except OllamaUnavailableError as exc:
        print(f"  FAILED - {exc}")
        return
    except Exception as exc:
        print(f"  FAILED - {exc}")
        return

    rate = result["tokens_per_second"]
    print(f"  model load       : {result['load_seconds']:.0f}s")
    print(f"  generation       : {rate:.1f} tokens/second")

    # A research or analysis stage generates roughly 800-2000 tokens of JSON.
    for label, tokens in (("a research stage", 1200), ("the gap analysis", 2000)):
        print(f"  estimated {label:17}: ~{tokens / max(rate, 0.1) / 60:.0f} min")

    print()
    if rate >= 15:
        print("  Verdict: comfortable. You can raise AFSGAP_LOCAL_MAX_PAGES to 8 and")
        print("           AFSGAP_LOCAL_PROMPT_CHARS to 24000 for richer research,")
        print("           and a 14B model would likely still be practical.")
    elif rate >= 6:
        print("  Verdict: workable at the current defaults. A full run is roughly")
        print("           15-30 minutes. Leave the stage cache on and iterate.")
    elif rate >= 2:
        print("  Verdict: slow. Reduce the work per stage:")
        print("             AFSGAP_LOCAL_MAX_PAGES=3")
        print("             AFSGAP_LOCAL_PROMPT_CHARS=8000")
        print("             AFSGAP_LOCAL_MAX_ITEMS=4")
        print("           Or move to a smaller model (qwen2.5:3b, llama3.2:3b).")
    else:
        print("  Verdict: too slow for this workload. A single stage would take over")
        print("           an hour. Use a 3B model, or run the hosted backend for the")
        print("           analysis and keep local mode for testing the pipeline.")


def _doctor(settings, llm: str | None, search: str | None, bench: bool = False) -> int:
    """Check every moving part of a local setup before a real run."""
    ok = True
    backend = (llm or settings.llm_backend or "claude").lower()
    print(f"Generation backend : {backend}")

    if backend == "ollama":
        from .llm.ollama import OllamaClient, OllamaUnavailableError
        from .search.base import build_backend

        client = OllamaClient(settings, build_backend(settings, search))
        print(f"  host             : {client.host}")
        print(f"  model            : {client.model}")
        try:
            client.check()
            print("  status           : OK")
            print(f"  keep alive       : {settings.ollama_keep_alive}")
            print(f"  input budget     : {settings.local_max_pages} pages x "
                  f"{settings.local_page_chars} chars, prompt cap {settings.local_prompt_chars}")
        except OllamaUnavailableError as exc:
            ok = False
            print(f"  status           : FAILED - {exc}")
            bench = False
    else:
        import os

        has_key = bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))
        print(f"  ANTHROPIC_API_KEY: {'set' if has_key else 'NOT SET'}")
        ok = ok and has_key

    # TLS comes before the network checks: on a managed laptop it is the thing
    # that breaks every one of them at once.
    import os

    from .net import TLS_HELP, build_session, is_tls_trust_error

    print("TLS trust          : ", end="")
    if settings.insecure_tls:
        print("VERIFICATION DISABLED (AFSGAP_INSECURE_TLS) - do not leave this on")
    elif settings.ca_bundle:
        print(f"CA bundle {settings.ca_bundle}")
    else:
        try:
            import truststore  # noqa: F401

            print("operating system certificate store (truststore installed)")
        except ImportError:
            print("Python default (certifi) - no corporate CA")
    proxy = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy")
    if proxy:
        print(f"  proxy            : {proxy}")

    tls_broken = False
    try:
        build_session(settings).get("https://help.sap.com", timeout=15)
        print("  reachability     : OK (https://help.sap.com)")
    except Exception as exc:
        ok = False
        if is_tls_trust_error(exc):
            tls_broken = True
            print("  reachability     : FAILED - corporate certificate authority not trusted")
        else:
            print(f"  reachability     : FAILED - {str(exc)[:110]}")

    # Test each link of the search chain separately: knowing *which* engine is
    # blocked is the difference between a config change and a support ticket.
    search_name = (search or settings.search_backend or "duckduckgo").lower()
    print(f"Search backend     : {search_name}")
    if search_name != "none":
        from .search.base import _build_one

        any_worked = False
        for part in [p.strip() for p in search_name.split(",") if p.strip()]:
            try:
                one = _build_one(settings, part)
                results = one.search("SAP returns process flow", max_results=3)
            except Exception as exc:
                print(f"  {part:<16} : FAILED - {str(exc)[:80]}")
                continue
            if results:
                any_worked = True
                print(f"  {part:<16} : OK ({len(results)} result(s), e.g. {results[0].url[:60]})")
            elif part == "seeds":
                print(f"  {part:<16} : no seed URLs yet - add them to {settings.seed_sources_path}")
            else:
                print(f"  {part:<16} : no results - "
                      + ("blocked by the TLS problem above" if tls_broken else "blocked or rate-limited"))
        if not any_worked:
            ok = False
            print("  none of the search backends returned anything; see docs/CORPORATE_NETWORK.md")

    from .sources.confluence import normalise_base_url

    configured = settings.confluence_base_url
    effective = normalise_base_url(configured)
    print("Confluence         : " + (effective or "not configured (runs will be greenfield)"))
    if effective:
        if effective != configured.rstrip("/"):
            print(f"  note             : corrected from {configured} - Cloud serves the API under /wiki")
        print(f"  auth             : {settings.confluence_auth}"
              f"{' + email' if settings.confluence_email else ''}"
              f"{' + token' if settings.confluence_api_token else ' (NO TOKEN)'}")
    print()
    print("Result             : " + ("ready" if ok else "not ready - fix the FAILED lines above"))
    if tls_broken:
        print()
        print(TLS_HELP)
    if bench and backend == "ollama":
        from .llm.ollama import OllamaClient
        from .search.base import build_backend

        _bench(settings, OllamaClient(settings, build_backend(settings, "none")))
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-7s %(name)s: %(message)s",
    )
    # The HTTP client inside the search library logs every provider request at
    # INFO, which buries our own output under dozens of lines per query.
    for noisy in ("primp", "ddgs", "urllib3", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.DEBUG if args.verbose else logging.WARNING)

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

    if args.command == "export-ca-bundle":
        from .net import export_ca_bundle

        try:
            path, added = export_ca_bundle(Path(args.out))
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 3
        print(f"Wrote {path}")
        print(f"  {added} certificate(s) from the machine's certificate store, plus the defaults.")
        print()
        print("Add this line to your .env, then re-run `python -m afsgap doctor`:")
        print(f"  AFSGAP_CA_BUNDLE={path}")
        return 0

    if args.command == "doctor":
        return _doctor(settings, args.llm, args.search, bench=args.bench)

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
        client = _build_llm(settings, args.llm, args.search)

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
    except ExcludedProcessError as exc:
        print(f"\nThis process is out of scope.\n\n{exc}\n", file=sys.stderr)
        return 4
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
