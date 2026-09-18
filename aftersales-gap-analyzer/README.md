# afsgap - Automotive Aftersales Process Gap Analyzer

Analyses an automotive aftersales logistics process against **SAP standard** and
**industry practice**, and produces a design document that says whether the
process as run today is best in the market or can be improved - with every claim
traceable to a source.

Built for the SAP ECC to SAP S/4HANA move: each gap carries an S/4HANA
disposition (fit to standard, configure, extend, keep custom, retire).

```
process YAML ─► pre-filter ─► SAP research ─► industry research ─► gap analysis ─► design doc
                (tolerance)   (SAP domains    (broad search then    (3-way)        (+ final gate)
                               only, T-code    allowlist filter
                               verification)   + OpenAlex, KPI-free)
```

## What this tool guarantees

| Guarantee | How it is enforced |
| --- | --- |
| **Tolerance topics never appear** - not in the input, the research, the analysis or the output | Four checkpoints: input pre-filter, research-snippet pre-filter, an explicit prompt rule, and post-generation re-scanning; a final gate fails the run if anything survives |
| **A T-code only appears with proof** | Candidate must match SAP transaction-code shape, be cited from an official public SAP domain, **and** be literally present in the fetched page text. Failures are dropped and listed with the reason |
| **Industry content is KPI-free** | Numeric and metric vocabulary is stripped pre- and post-generation; the rendered industry section is re-checked by the final gate |
| **Industry research is broad, then filtered** | Ten differently-framed web queries plus five OpenAlex queries, searched unrestricted, then filtered against a credible-source allowlist. Rejected sources are published in the report |
| **SAP facts come only from SAP** | Web search is domain-restricted to official SAP properties; SAP area names (SD, EWM, CMH, master data, dealer front-end/SOp, aftersales) are used **only** to build queries, never asserted |

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                  # add your ANTHROPIC_API_KEY

# try it with no API key and no network:
python -m afsgap run data/processes/defective_parts_return.yaml --offline

# the real thing:
python -m afsgap run data/processes/defective_parts_return.yaml
```

Output lands in `output/`:

* `<process>_<date>_design_document.md` - the design document
* `<process>_<date>_design_document.docx` - same, for circulation
* `<process>_<date>_run.json` - the full machine-readable run record

## Commands

```bash
python -m afsgap run <process.yaml> [--offline] [--no-cache] [--skip-industry] [-v]
python -m afsgap list-processes
python -m afsgap check-filters "the delivery tolerance is checked at receipt"
```

`--no-cache` forces fresh research; without it, completed research stages are
reused from `.cache/stages/` so you can iterate on analysis and reporting for free.

## Adding a process

Copy any file in `data/processes/` and edit it. The only required fields are
`process_id`, `process_name` and `steps`; everything else improves the analysis.
Write the AS-IS honestly, including pain points - they drive the gap severity.

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
* `docs/PYCHARM_SETUP.md` - local setup, run configurations, debugging
* `docs/WEEKEND_PLAN.md` - hour-by-hour plan to finish this weekend
* `docs/METHODOLOGY.md` - how to defend the output in a design review

## Tests

```bash
python -m pytest
```

The suite runs fully offline: no API key, no network.
