# Offline fixtures

Synthetic data used by `--offline` runs and by the test suite. They exist to
exercise the wiring - filters, T-code verification, rendering, the final gate -
without spending API calls.

**Nothing here is a real research finding.** The SAP and industry statements are
placeholders. `pages.json` supplies fake page text so that T-code verification
can run without network access; `afsgap.llm.offline.seed_page_cache` writes it
into the page cache under the same hashing scheme the live fetcher uses.

Deliberate defects are baked in so the guards are observable on every offline
run: one tolerance sentence, one KPI sentence, and four T-code candidates of
which only two can pass verification.
