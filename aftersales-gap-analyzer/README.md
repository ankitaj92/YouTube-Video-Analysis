# afsgap - Automotive Aftersales Process Gap Analyzer

You give it a **process name**. It reads that process from **Confluence**,
researches **SAP standard** and **industry practice**, and produces a design
document - with every claim traceable to a source.

If the process does not exist in Confluence, it says so and switches to
**greenfield mode**: instead of analysing gaps, it proposes how to implement the
process from SAP standard plus leading practice.

Built for the SAP ECC to SAP S/4HANA move: each gap carries an S/4HANA
disposition (fit to standard, configure, extend, keep custom, retire).

```
                            ┌─ found ────► AS-IS ─┐
process name ─► Confluence ─┤                     ├─► SAP research ─► industry research ─┐
                            └─ not found ─────────┘   (SAP domains    (broad search then  │
                                    │                  only, T-code    allowlist filter   │
                                    │                  verification)   + OpenAlex)        │
                                    │                                                    │
                     ┌──────────────┴──────────────┐                                     │
                     ▼                             ▼                                     │
              gap analysis  ◄────────────────  greenfield blueprint  ◄───────────────────┘
              (3-way, verdict)                (design decisions)
                     └──────────────┬──────────────┘
                                    ▼
                        design document + final gate
```

## What this tool guarantees

| Guarantee | How it is enforced |
| --- | --- |
| **Tolerance configuration never appears** - not in the input, the research, the analysis or the output | Four checkpoints: input pre-filter, research-snippet pre-filter, an explicit prompt rule, and post-generation re-scanning; a final gate fails the run if anything survives. Over/under-delivery terms are contextual, so the *business events* stay analysable - see `docs/METHODOLOGY.md` |
| **A T-code only appears with proof** | Candidate must match SAP transaction-code shape, be cited from an official public SAP domain, **and** be literally present in the fetched page text. Failures are dropped and listed with the reason |
| **Industry content is KPI-free** | Numeric and metric vocabulary is stripped pre- and post-generation; the rendered industry section is re-checked by the final gate |
| **Industry research is broad, then filtered** | Ten differently-framed web queries plus five OpenAlex queries, searched unrestricted, then filtered against a credible-source allowlist. Rejected sources are published in the report |
| **SAP facts come only from SAP** | Web search is domain-restricted to official SAP properties; SAP area names (SD, EWM, CMH, master data, dealer front-end/SOp, aftersales) are used **only** to build queries, never asserted |
| **"This process is new" is evidenced, not assumed** | The provenance section publishes what was searched, in which spaces, every candidate page seen and its match score, and why each was rejected |
| **Evidence quotes are checked against the page** | A quote that is not literally present in the source it cites is dropped and logged (`unverified_quote`); the claim survives, the false citation does not |
| **Confluence pages are transcribed, not improved** | The extraction prompt is a transcriber: it may not add steps the page does not contain, so a thin page produces a thin AS-IS - which is itself a finding |

## Two ways to run it

| | Hosted | Local |
| --- | --- | --- |
| Generation | Claude API (`claude-opus-5`) | Ollama on your machine |
| Search | Claude's server-side web search | DuckDuckGo, Mojeek, SearXNG, or your own seed URLs |
| Your process content | sent to the API | **never leaves the laptop** |
| Quality | best | usable for testing; see `docs/LOCAL_MODE.md` |

```bash
python -m afsgap run "Defective Parts Return"                # hosted
python -m afsgap run "Defective Parts Return" --llm ollama   # fully local
python -m afsgap doctor --llm ollama                         # check the local setup
python -m afsgap doctor --llm ollama --bench                 # measure what this machine can do
```

Both paths share everything else: the same prompts, exclusions, T-code
verification and document.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env            # ANTHROPIC_API_KEY + the Confluence settings

# try it with no API key, no Confluence and no network:
python -m afsgap run "Defective Parts Return" --offline    # found    -> gap analysis
python -m afsgap run "Battery Pack Return" --offline       # not found -> blueprint

# the real thing:
python -m afsgap run "Defective Parts Return"
```

Output lands in `output/`:

* `<process>_<date>_design_document.md` / `.docx` - the gap analysis document
* `<process>_<date>_blueprint.md` / `.docx` - the greenfield document, when the
  process was not found
* `<process>_<date>_run.json` - the full machine-readable run record

## Commands

```bash
python -m afsgap run "<process name>"      # read the process from Confluence
python -m afsgap run <process.yaml>        # or from a local definition
python -m afsgap confluence-search "<process name>"   # preview what would be matched
python -m afsgap search-test "SAP returns process" --sap   # debug search quality

# run options
  --page-id 123456     use a specific Confluence page instead of searching
  --space AFTS         restrict the search to a space (repeatable)
  --new                skip the lookup and design the process from scratch
  --require-existing   fail rather than fall back to greenfield design
  --search LIST        search backend or chain: duckduckgo,mojeek,searxng,seeds,none
  --offline            use fixtures - no API key, no Confluence, no network
  --no-cache           ignore cached research stages
  --skip-industry      SAP research and analysis only
```

`--no-cache` forces fresh research; without it, completed research stages are
reused from `.cache/stages/` so you can iterate on analysis and reporting for free.

## Where the process comes from

**Confluence (default).** Give a process name; the tool searches Confluence
(title first, then full text, optionally within named spaces), scores candidates
by title overlap, and reads the best match above the threshold. The page is
transcribed into a structured AS-IS - it is never "improved" during transcription.
See `docs/CONFLUENCE.md`.

**A local YAML file.** Pass a path instead of a name. Useful when a process is
not documented in Confluence but you have it written down, and for regression
runs. Copy any file in `data/processes/` as a starting point.

**Nothing (greenfield).** When no page matches, the run switches to blueprint
mode and designs the process. Force it with `--new`; refuse it with
`--require-existing`.

## Tuning without touching code

| File | Controls |
| --- | --- |
| `afsgap/resources/tolerance_terms.yaml` | what counts as a tolerance topic |
| `afsgap/resources/kpi_terms.yaml` | what counts as a KPI or metric |
| `afsgap/resources/sources.yaml` | SAP official domains, the credible-industry allowlist, denied domains |
| `afsgap/resources/sap_area_hints.yaml` | which SAP areas the search probes (**guidance only**) |

Use `python -m afsgap check-filters "<text>"` to see the effect of an edit immediately.

## Documentation

* `docs/ARCHITECTURE.md` - how the pipeline and the guards fit together
* `docs/CONFLUENCE.md` - connecting Confluence, matching, and what makes a good process page
* `docs/LOCAL_MODE.md` - running entirely on your own machine with Ollama and DuckDuckGo
* `docs/CORPORATE_NETWORK.md` - TLS interception, proxies, and `CERTIFICATE_VERIFY_FAILED`
* `docs/EVALS.md` - the eval set: how output quality is measured and how to extend it
* `docs/PRODUCTION_READINESS.md` - what is solid, what is missing, what is unverified
* `docs/PYCHARM_SETUP.md` - local setup, run configurations, debugging
* `docs/WEEKEND_PLAN.md` - hour-by-hour plan to finish this weekend
* `docs/METHODOLOGY.md` - how to defend the output in a design review

## Tests and evals

```bash
python -m pytest                                    # 257 tests, fully offline
python -m afsgap.evals run --offline --tags offline # does the output hold up?
```

The tests check mechanics; the eval set checks whether the analysis is any good.
Most of its expectations derive from your own process definitions - every
documented pain point must be addressed, every custom object dispositioned. See
`docs/EVALS.md`.
