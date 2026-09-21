# Reading processes from Confluence

## Connecting

Add to `.env`:

```ini
# Confluence Cloud
AFSGAP_CONFLUENCE_BASE_URL=https://yourcompany.atlassian.net/wiki
AFSGAP_CONFLUENCE_EMAIL=you@yourcompany.com
AFSGAP_CONFLUENCE_API_TOKEN=<API token from id.atlassian.com>
AFSGAP_CONFLUENCE_AUTH=basic

# or Confluence Data Center / Server
AFSGAP_CONFLUENCE_BASE_URL=https://confluence.yourcompany.com
AFSGAP_CONFLUENCE_API_TOKEN=<personal access token>
AFSGAP_CONFLUENCE_AUTH=bearer
```

Both use the v1 content API (`/rest/api/content/...`), which exists on Cloud and
Data Center alike - the only differences are the base URL and the auth scheme.

> **The `/wiki` trap.** Confluence Cloud serves its API under `/wiki`. If you set
> the base URL to `https://yoursite.atlassian.net` without it, every call returns
> **404** - which reads like "the API is gone" rather than "the path is short".
> afsgap appends `/wiki` automatically for `*.atlassian.net` and `doctor` prints
> the correction it made, but set it correctly in `.env` anyway. Data Center
> serves the API at the root, so nothing is added there.

Restrict the search to the spaces that actually hold process documentation:

```ini
AFSGAP_CONFLUENCE_SPACES=AFTS,LOG
```

This is worth doing. It speeds up the search, and it stops a meeting note in a
project space from being mistaken for the process of record.

**The tool only reads.** It never writes, comments or moves anything, and it
needs no more than read access to those spaces.

## How a page is chosen

1. Two CQL searches: `title ~ "<name>"`, then `text ~ "<name>"`, both restricted
   to `type = page` and to your spaces if set.
2. Each hit is scored on title-token overlap with the process name (stop words
   like "the" and "process" are ignored). Exact-enough titles score 1.0.
3. The best page at or above `AFSGAP_CONFLUENCE_MIN_MATCH` (default 0.45) wins.
4. Below the threshold, the run switches to greenfield mode.

Preview the decision before spending a run:

```bash
python -m afsgap confluence-search "Defective Parts Return"
```

Every candidate, its score and the reason for the outcome are published in
section 2 of the document, so "we treated this as a new process" is always
challengeable.

Overrides:

* `--page-id 123456` - use exactly this page, skip searching.
* `--space AFTS` - narrow one run without changing `.env`.
* `--require-existing` - fail loudly rather than design from scratch (use this
  in a batch run where silently designing a process would be wrong).
* Lower `AFSGAP_CONFLUENCE_MIN_MATCH` if your pages are titled loosely.

## What makes a page work well

The extraction step is deliberately a *transcriber*: it records what the page
says and may not add steps, systems or controls that are not there. So page
quality maps directly to analysis quality.

Pages that work well have:

* **A step table.** Columns for step, actor, system and notes are read cleanly -
  tables survive the conversion as rows.
* **Headings** for purpose, scope, roles, known issues.
* **Named systems** at each step, rather than "the system".
* **Problems written down.** Anything flagged as a pain point drives gap severity.
* **Custom objects named** (Z-tables, Z-reports, enhancements, interfaces) -
  these produce the retire/keep recommendations.

Pages that work badly: screenshots only (there is no text to read), a single
prose paragraph, or a page that mixes five processes together. If a run produces
a thin AS-IS, the document says so in a warning - fix the page, not the tool.

Confluence macros that produce layout rather than content (TOC, page trees,
child lists) are dropped. Text inside the page is treated strictly as data: if a
page happens to contain instructions addressed to an AI, they are ignored.

## Caching and freshness

Fetched pages are cached under `.cache/confluence/<page-id>.json`. Delete that
file (or the folder) to pick up an edit. The page version and last-modified date
are printed in section 2 of every document, so a reader can tell how stale the
source was - useful when a process page has not been touched in two years.

## If Confluence is unreachable

A missing configuration or a failed connection is not a hard error by default:
the run records why, warns, and continues in greenfield mode. That is the right
behaviour for a workshop where the VPN drops - but check section 2 before
believing a "new process" verdict. Use `--require-existing` when you want the
run to stop instead.
