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
ollama pull qwen2.5:7b

# 3. install the Python side
pip install -r requirements.txt

# 4. check everything is reachable before spending a run
python -m afsgap doctor --llm ollama

# 5. see what this machine can actually sustain
python -m afsgap doctor --llm ollama --bench
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
AFSGAP_OLLAMA_MODEL=qwen2.5:7b
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
| `qwen2.5:7b` | The default. Realistic on a laptop without a GPU, and good enough at constrained JSON. |
| `qwen2.5:14b` | Better analysis. Worth it only if `doctor --bench` shows comfortable throughput. |
| `qwen2.5:3b`, `llama3.2:3b` | When 7B is too slow. Expect more schema retries and blunter analysis. |
| `llama3.1:8b`, `mistral-nemo` | Fine alternatives at the 7-8B tier. |
| `qwen2.5:32b` and up | Only with a real GPU. |

Start at the default, run `doctor --bench`, and move up or down from there
rather than guessing.

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

## Speed: measure first

```bash
python -m afsgap doctor --bench
```

This loads the model, measures tokens per second, and translates that into what
a run will actually cost you - a stage generates roughly 800-2000 tokens of
JSON, so the rate tells you whether a run is fifteen minutes or four hours. It
then recommends settings for your machine.

Rough guide:

| tokens/sec | What to do |
| --- | --- |
| 15+ | Raise `AFSGAP_LOCAL_MAX_PAGES` to 8 and `AFSGAP_LOCAL_PROMPT_CHARS` to 24000; a 14B model is practical |
| 6-15 | Defaults are right. A full run is roughly 15-30 minutes |
| 2-6 | Cut the work: `AFSGAP_LOCAL_MAX_PAGES=3`, `AFSGAP_LOCAL_PROMPT_CHARS=8000`, `AFSGAP_LOCAL_MAX_ITEMS=4`, or a 3B model |
| under 2 | Too slow for this workload. Use a 3B model, or run the analysis hosted and keep local mode for testing |

### Why a stage can take a long time

Three things drive it, in order:

1. **How much you ask it to write.** Output dominates: constrained JSON
   generation is slower than reading, and every extra list item costs tokens.
   `AFSGAP_LOCAL_COMPACT=true` (default) caps list lengths and description
   lengths, and is the single biggest lever.
2. **How much you feed it.** Four pages of 2500 characters is about 2500 tokens
   of input. The old defaults sent four times that.
3. **Reloading the model.** Ollama unloads after a few idle minutes; reloading a
   7B model can take longer than the generation. `AFSGAP_OLLAMA_KEEP_ALIVE=30m`
   keeps it resident, and the model is preloaded before the first stage so that
   the load time does not look like a hang.

Responses stream, so the timeouts describe two different waits:

* `AFSGAP_OLLAMA_FIRST_TOKEN_TIMEOUT` (900s) covers **reading the prompt**.
  Nothing is emitted during this phase, and on a CPU a few thousand tokens of
  input can take many minutes. It is not a hang, and it is the wait that most
  often ends a local run.
* `AFSGAP_OLLAMA_CHUNK_TIMEOUT` (180s) covers the gap **between** tokens once
  generation starts. Those gaps are small, so a breach here is a real stall.

The log tells you which phase you are in - the prompt size when it starts
reading, then "first token after Ns" when generation begins, then a token count
and rate every 30 seconds.

## Tuning

| Symptom | Setting |
| --- | --- |
| "generating for Ns without finishing" | the model is working, just slowly - run `doctor --bench` and apply its recommendation, or lower `AFSGAP_LOCAL_MAX_ITEMS` |
| "produced no output within Ns while reading a N character prompt" | **the usual local-mode failure.** Reading the prompt happens before the first token and is the slow part on a CPU. Give it less to read (`AFSGAP_LOCAL_PROMPT_CHARS=6000`, `AFSGAP_LOCAL_MAX_PAGES=2`) or use a 3B model; raising `AFSGAP_OLLAMA_FIRST_TOKEN_TIMEOUT` only buys patience |
| "stopped mid-answer after N tokens" | memory pressure - use a smaller model |
| JSON cut off mid-structure | raise `AFSGAP_OLLAMA_NUM_PREDICT` |
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
