# Running fully locally (Ollama + DuckDuckGo)

Local mode replaces the hosted Claude API with a model running on your own
machine, and Claude's server-side web search with DuckDuckGo. **No process
content leaves the laptop** - not the Confluence page, not the AS-IS, not the
analysis. The only outbound traffic is the search queries themselves and the
page fetches they lead to, which carry search terms like
"SAP S/4HANA returns process flow" and nothing of yours.

Everything else is unchanged: the same prompts, the same tolerance and KPI
exclusions, the same T-code verification, the same document.

## Setup

On a managed work laptop, run `python -m afsgap doctor` first - corporate TLS
interception breaks every outbound call and has its own guide,
`docs/CORPORATE_NETWORK.md`.

```bash
# 1. install Ollama            https://ollama.com/download
ollama serve                   # usually already running as a service

# 2. pull a model
ollama pull qwen2.5:14b

# 3. install the Python side
pip install -r requirements.txt

# 4. check everything is reachable before spending a run
python -m afsgap doctor --llm ollama
```

`doctor` verifies the Ollama host, that the model is actually pulled, that the
search backend returns results, and whether Confluence is configured. Fix
anything it marks FAILED before running.

## Running

```bash
python -m afsgap run "Defective Parts Return" --llm ollama
python -m afsgap run "Battery Pack Return" --llm ollama --new
```

Or make it the default in `.env`:

```ini
AFSGAP_LLM=ollama
AFSGAP_SEARCH=duckduckgo
AFSGAP_OLLAMA_MODEL=qwen2.5:14b
```

## How local mode differs internally

The hosted path gives the model a web-search tool and lets it choose its own
queries. Ollama has no such tool, so retrieval moves into the pipeline:

| | Hosted (Claude) | Local (Ollama) |
| --- | --- | --- |
| Who picks the queries | the model | the pipeline (`build_sap_queries`, the industry variants) |
| Who runs the search | Anthropic's server tool | DuckDuckGo, from your machine |
| Model calls per research stage | 2 (research + extract) | 1 (extract only) |
| What the model sees | search results and its own citations | the text of the pages that were actually fetched |

The local arrangement is arguably the more auditable of the two: the queries are
exactly the ones in the code, and the model only ever sees retrieved text.

## Quote verification

A smaller model is likelier to paraphrase a source and present it as a quote.
So on the local path every evidence quote is checked against the text of the
page it cites (normalised, with tolerance for small drift). A quote that is not
there is dropped and recorded in the exclusion log as `unverified_quote` - the
claim survives, its false citation does not. Turn it off with
`AFSGAP_VERIFY_QUOTES=false` if you are debugging, not for a real deliverable.

T-code verification is unchanged and was never model-dependent: it reads the
fetched SAP page and looks for the literal code.

## Choosing a model

| Model | Verdict |
| --- | --- |
| `qwen2.5:14b` | Good default. Handles the nested JSON schemas reliably. |
| `llama3.1:8b`, `mistral-nemo` | Usable. Expect occasional schema retries; the client retries automatically. |
| 7B and below | Struggles with the deeper schemas (gap analysis, blueprint). Fine for a smoke test, not for output you will show anyone. |
| `qwen2.5:32b`, `llama3.3:70b` | Better analysis if you have the RAM and the patience. |

Structured output is enforced by handing Ollama the JSON schema in `format`, so
the model is grammar-constrained rather than asked politely for JSON. If the
result still does not validate, the client retries with the validation error
attached, up to `AFSGAP_OLLAMA_MAX_ATTEMPTS`.

## Expectations - read this before judging the output

A 14B local model is not going to match a frontier model at this task. What
degrades, roughly in order:

1. **Synthesis quality.** Gaps are blunter, recommendations more generic, and
   the verdict rationale thinner.
2. **Instruction adherence.** The exclusions are enforced in code, so a slip
   gets caught - but you will see more `[tolerance]` and `[kpi]` post-generation
   warnings in the log than with the hosted model. That log is the tell: many
   warnings means the model is fighting the prompt.
3. **Citation discipline.** Hence quote verification.

What does *not* degrade: the sourcing guarantees. T-codes still need a literal
match on an official SAP page, industry sources still have to pass the
allowlist, and tolerance content is still excluded at four checkpoints.

Treat local mode as the way to test the pipeline, tune the filters, and
demonstrate the concept without a data-transfer conversation - then decide
whether the analysis quality justifies a hosted run later.

## Tuning

| Symptom | Setting |
| --- | --- |
| Ollama times out | raise `AFSGAP_OLLAMA_TIMEOUT`, or lower `AFSGAP_LOCAL_MAX_PAGES` |
| Stages look truncated | raise `AFSGAP_OLLAMA_NUM_CTX` (and check the model supports it) |
| Prompt trimming in the log | lower `AFSGAP_LOCAL_PAGE_CHARS` or `AFSGAP_LOCAL_MAX_PAGES` |
| `CERTIFICATE_VERIFY_FAILED` on every call | corporate TLS interception - see `docs/CORPORATE_NETWORK.md`; usually `pip install truststore` is the whole fix |
| DuckDuckGo returns nothing | rule out TLS first (`python -m afsgap doctor`), then rate limiting: raise `AFSGAP_SEARCH_PAUSE` and re-run - stages are cached, so you keep what already worked |
| Want zero outbound traffic | `--search none` plus `AFSGAP_OPENALEX_ENABLED=false`; the run still completes and the document states that research returned nothing |

## What this does not solve

Confluence still has to be reachable, and your Confluence token is still a
credential on the laptop. Local mode removes the *third-party* data transfer,
not the need to handle internal content carefully. `output/` and
`.cache/confluence/` still hold copies of your process documentation.
