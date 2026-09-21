# Production readiness - an honest assessment

**Short answer: this is a solid internal tool, not production software.** It is
ready for an analyst or a small programme team to run on a laptop and produce
documents a design review can take seriously. It is not ready to be deployed as
a service, handed to fifty users, or run unattended on a schedule.

The distinction matters, so here is the detail.

## What is genuinely solid

| Area | State |
| --- | --- |
| Correctness guarantees | The sourcing rules - tolerance exclusion, KPI-free industry content, literal T-code verification, evidence-quote verification, source allowlists - are enforced in code at four checkpoints and covered by ~220 tests |
| Determinism | Every run writes a full machine-readable record (`_run.json`) with the queries used, sources kept, sources rejected and every excluded segment. A reviewer can reconstruct how any statement got there |
| Failure behaviour | Network, TLS, search and model failures degrade to a document that states what was missing, rather than to a crash or to invented content |
| Separation | Generation and retrieval are pluggable; the pipeline does not know which backend it is using. Swapping Claude for Ollama, or DuckDuckGo for seed URLs, changes nothing downstream |
| Configuration | Behaviour is in editable YAML and environment variables, not buried in code |
| Test suite | ~220 tests, no network, no API key, no local model required. Runs in about 15 seconds |

There are no bare `except:` clauses, no `eval`/`exec`, no `subprocess`, and no
`verify=False` anywhere in the package.

## What is missing for production

**No CI.** Tests exist but nothing runs them automatically. A GitHub Actions
workflow is perhaps 20 lines; without it, the suite protects you only when you
remember to run it.

**No integration tests.** Every test is offline against fixtures and stubs. The
Confluence client, the live search backends and the Ollama client have never
been exercised against the real thing in a test - only by hand, by you. That is
the single biggest gap, and it is why each of the last few rounds found bugs in
exactly those three places.

**Search is scraping, and scraping is fragile.** DuckDuckGo, Mojeek and the
`ddgs` library are not contracts. They change, rate-limit, and block. The
`ddgs` upgrade that broke your notebook is the normal condition of this
dependency, not an anomaly. For anything long-lived, use a search API with terms
(Brave Search API, Bing, SerpAPI) or the `seeds` backend, where you control the
inputs.

**Output quality evaluation exists now, and is young.** `docs/EVALS.md`
describes a ten-case eval set scoring compliance, grounding and insight
separately, with most expectations derived from your own process definitions.
It is a real regression net, but it is ten cases graded mostly by keyword
matching - enough to catch a change that breaks things, not enough to certify
that an analysis is excellent. Growing the set and using the model judge on a
baseline is the next increment.

**Single user, single process.** No concurrency, no queue, no locking on the
cache directory. Two runs against the same process at once will race on
`.cache/stages`. Fine for one analyst; not fine as a service.

**Secrets are `.env` files.** Appropriate for a laptop, not for shared or
server use, where a secrets manager and short-lived credentials belong.

**No observability.** Logging is human-readable text. There are no metrics, no
structured events, no run history beyond the JSON files in `output/`.

**Local model quality is unproven.** The pipeline works with Ollama; whether a
7B model's analysis is worth reading is untested and workload-specific. Measure
it before trusting it (`doctor --bench` covers speed, not quality).

## What I could not verify

I have never run this against your live systems. Specifically untested against
reality: Confluence (Cloud or Data Center), live DuckDuckGo or any search engine,
a real Ollama model, and the Anthropic API. The code paths are unit-tested with
stubs that mirror the documented wire formats, and the wire formats were read
from the libraries themselves rather than recalled - but a stub agreeing with my
reading of an API is not the same as the API agreeing.

Treat the first live run of each of those as the real test.

## If you want to take it to production

In order of value:

1. **CI** running the test suite and the offline eval subset on every push
   (the eval exits 2 on a blocking failure, so it drops straight in).
2. **Grow the eval set** beyond ten cases and run the model judge against a
   labelled baseline.
3. **A search API with terms of service**, replacing the scraped backends.
4. **Integration tests** against a Confluence sandbox and a small local model.
5. **Packaging and pinning** - `pyproject.toml` exists; pin exact versions in a
   lockfile so a `ddgs` release cannot change behaviour underneath you again.
6. **A golden-document test**: run a fixed process end to end and diff the
   rendered document, so unintended changes in output surface immediately.

## Verdict

For the job you described - analysing aftersales processes for an S/4HANA
programme, run by you or your team, with a human reading every document before
it goes anywhere - it is fit for purpose today, provided you treat the output as
a well-sourced draft rather than a finished deliverable.

For anything beyond that, the list above is the work.
