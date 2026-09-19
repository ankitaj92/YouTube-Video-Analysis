# Architecture

## Stage flow

| # | Stage | Module | Input | Output |
| --- | --- | --- | --- | --- |
| 1 | Resolve the process | `sources/resolver.py` | process name or YAML path | `CurrentProcess` + provenance, **or** a greenfield decision |
| 2 | SAP standard research | `research/sap.py` | `CurrentProcess` | `SapResearchResult` |
| 3 | Industry benchmark research | `research/industry.py` | `CurrentProcess` | `IndustryResearchResult` |
| 4a | Gap analysis (AS-IS exists) | `analysis/gap.py` | all three | `GapAnalysis` |
| 4b | Greenfield design (no AS-IS) | `analysis/blueprint.py` | SAP + industry | `ProcessBlueprint` |
| 5 | Render + final gate | `report/`, `validation.py` | `RunResult` | Markdown, DOCX, JSON |

Stages 2 and 3 are cached under `.cache/stages/`, so iterating on stages 4-5
costs nothing.

## The two modes

Stage 1 decides which document the run produces:

```
process name ──► Confluence search (title, then full text, optionally by space)
                      │
                      ├─ best match >= threshold ──► transcribe page ──► GAP MODE
                      │                                                  (verdict on the current process)
                      └─ nothing credible ─────────────────────────────► GREENFIELD MODE
                                                                         (blueprint: how to implement it)
```

Both modes run the same SAP and industry research and share the same evidence,
exclusion and validation machinery - only the analysis stage and the middle of
the document differ. The greenfield branch is a *finding*, not an error path:
the provenance record carries what was searched, which spaces, every candidate
and its score, and why each was rejected, so the claim "this process is new" can
be challenged from the document alone.

`--require-existing` turns the fallback into a hard failure, for batch runs where
silently designing a process would be the wrong outcome.

## Backends

Generation and retrieval are both pluggable, and the pipeline does not know
which is in use:

| | `afsgap/llm/client.py` (Claude) | `afsgap/llm/ollama.py` (local) | `afsgap/llm/offline.py` (fixtures) |
| --- | --- | --- | --- |
| `research()` | server-side web search, 1 model call | search backend + page fetch, **no model call** | canned transcript |
| `extract()` | `messages.parse` with a Pydantic schema | Ollama `format` = flattened JSON schema, with retries | canned JSON |

All three return the same `ResearchTranscript` (`afsgap/research/transcript.py`),
so filtering, verification, analysis and rendering are backend-agnostic.

The local path also populates `transcript.pages` - the retrieved page text -
which is what `research/quotes.py` checks evidence quotes against. A smaller
model is likelier to paraphrase a source into a quotation, so an unsupported
quote is dropped and logged rather than published.

Search backends live in `afsgap/search/`. `DuckDuckGoBackend` prefers the `ddgs`
package and falls back to DuckDuckGo's HTML endpoint; domain restriction is
applied twice, as `site:` operators to steer the engine and as a hard filter on
the results, because `site:` is a hint and an SAP fact from a non-SAP domain is
not an SAP fact.

## Reading Confluence

`sources/confluence.py` targets the v1 content API, which both Cloud and Data
Center serve, with Basic (Cloud email + token) or Bearer (DC PAT) auth. Page
bodies arrive as storage-format XHTML and go through `sources/storage_format.py`,
a standard-library parser that keeps the structure carrying the meaning -
headings, lists and above all **table rows**, since process pages document their
steps in tables. Layout macros (TOC, page trees) are dropped.

Transcription into `CurrentProcess` is an LLM extraction call with a transcriber
contract: it may not add steps the page does not contain. A thin page therefore
produces a thin AS-IS, which the run reports as a warning rather than papering
over. Page text is tolerance-filtered *before* it reaches that prompt, and the
extracted result is filtered again after.

## Why research and extraction are two separate calls

Each research stage runs **twice** against the model:

1. **Research call** - `web_search` server tool, free-form output. Everything the
   search returned is captured: result URLs, the model's citations with verbatim
   `cited_text`, and the queries it actually ran.
2. **Extraction call** - *no tools*, structured output validated against a
   Pydantic schema, and the only material in the prompt is what step 1 retrieved.

The split is what makes grounding checkable. Extraction cannot reach the open
web, so a T-code or a practice that appears in the structured output must have
come from the retrieved text - and the retrieved text was domain-filtered before
it got there.

## The four enforcement points

Both exclusions (tolerances everywhere, KPIs in industry content) are applied at
every point where content can enter or leave the system:

```
     input YAML            search results          model output         rendered doc
         │                       │                       │                    │
    ┌────▼────┐            ┌─────▼─────┐           ┌─────▼─────┐        ┌─────▼─────┐
    │ PRE     │            │ PRE       │           │ POST      │        │ FINAL     │
    │ filter  │            │ filter    │           │ validate  │        │ gate      │
    └─────────┘            └───────────┘           └───────────┘        └───────────┘
   scrub_input()          scrub_input()          validate_output()    validate_document()
```

Plus a fifth, softer one: `PROMPT_RULE` in `filters/tolerance.py` and
`filters/kpi.py` is embedded in every system prompt. The prompt rule reduces
breaches; the code guarantees they do not ship.

Every removal is recorded as an `ExclusionRecord` and printed in section 9 of the
design document, so a reviewer can see exactly what was taken out and why. The
exclusion log is exempt from the final gate (it necessarily quotes the excluded
text) - section 9 and `<!-- afsgap:meta -->` blocks are stripped before scanning.

## T-code verification

`research/tcode.py` implements two distinct checks:

* **Input side** (`TCodeVerifier.verify`) - shape, then official-SAP domain, then
  a literal whole-token match in the page text fetched from that URL. Pages are
  cached under `.cache/pages/` by URL hash.
* **Output side** (`find_tcode_mentions`) - scans generated prose for codes
  introduced by a transaction-code cue ("transaction VA01", "VA01 transaction"),
  after stripping URLs. Cue-based matching is deliberate: scanning for anything
  that merely *looks* like a code flags acronyms (`ABAP`, `DOI`) and section ids
  (`G01`). Rejected codes are additionally searched for without a cue, because a
  rejected code appearing anywhere is a hard failure.

`require_literal_tcode_evidence` can be disabled in config for a fast dry run.
Do not disable it for a real deliverable - it is the only thing standing between
a design document and a plausible-looking hallucinated transaction code.

## Source model

`filters/sources.py` classifies every URL into `sap_official`,
`industry_credible` (with a category), `scholarly`, `other` or `denied`.

* SAP research **restricts the search itself** to `sap_official` domains -
  an SAP fact from a third-party blog is not an SAP fact.
* Industry research **searches normally** and filters afterwards. This is
  deliberate: restricting the search up front biases what the engine returns and
  hides useful material behind unusual domains. Search wide, judge after,
  publish the rejects.

## Industry query strategy

`WEB_QUERY_VARIANTS` reframes the question ten ways - the process family, reverse
logistics, the dealer operating model, warranty context, network design, digital
enablers - rather than repeating the client's internal process name, which is
vocabulary no external publisher uses. `SCHOLARLY_QUERY_VARIANTS` drives a
separate OpenAlex leg, so peer-reviewed grounding does not depend on what a web
search engine happens to surface.

## Extending

* **New process** - add a YAML file to `data/processes/`.
* **New exclusion rule** - subclass `PatternFilter`, add a resource YAML, wire it
  into the stages that matter. `KpiFilter` is the smallest example to copy.
* **New SAP area to probe** - add an entry to `sap_area_hints.yaml`. It becomes
  search guidance only; nothing else changes.
* **Different report format** - `report/design_doc.py` renders from `RunResult`;
  add a renderer alongside `render_markdown`.
