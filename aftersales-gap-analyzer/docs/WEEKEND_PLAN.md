# Finishing this weekend

The code is complete and tested offline. What remains is *your* content: real
process definitions, one live run per process, and a review of what comes back.

## Saturday morning - setup and first live run (about 2 hours)

1. Follow `docs/PYCHARM_SETUP.md` (20 min).
2. Run both offline configurations. Read the two generated documents in
   `output/` end to end - they show you exactly what each deliverable looks like
   (25 min).
3. Connect Confluence (`docs/CONFLUENCE.md`) and check the matching before
   spending a run (15 min):
   ```bash
   python -m afsgap confluence-search "Defective Parts Return"
   ```
   If the right page is not starred, set `AFSGAP_CONFLUENCE_SPACES`, or plan to
   pass `--page-id`.
4. Do a **single live run** on Defective Parts Return (10 min of runtime):
   ```bash
   python -m afsgap run "Defective Parts Return" -v
   ```
5. Read section 2 (process source), section 4 (SAP standard) and section 9.4
   (queries run) critically:
   * Did it pick the right Confluence page? Section 2 lists what it rejected.
   * Is the transcribed AS-IS in section 3 what the page actually says?
   * Are the retrieved SAP pages the ones you would have opened yourself?
   * Did any T-code get rejected that you know is real? That is a research
     problem (the right page was not retrieved), not a verification bug - add a
     sharper query to `sap_area_hints.yaml` and re-run that stage.
   * Is section 4 genuinely qualitative?

## Saturday afternoon - make the AS-IS real (about 3 hours)

The single biggest quality lever is the accuracy of the AS-IS. Nothing the tool
does can compensate for a vague process description - and now that the AS-IS
comes from Confluence, this means **fixing the Confluence pages**.

For each process page, make sure it has: a step table with actor and system per
step, the known problems written down, and the custom ECC objects named. See
"What makes a page work well" in `docs/CONFLUENCE.md`. This is worth doing
regardless of this tool - it is the documentation debt the S/4HANA programme will
hit anyway.

Where a process is not in Confluence and you do not want to write the page yet,
use a local YAML file (`data/processes/`) for this weekend and move it into
Confluence later.

Budget roughly 45 minutes per process with the process owner.

## Saturday evening - the other processes (about 1 hour)

Run each process once. Cache means re-running the report is free:
```bash
python -m afsgap run "Dealer Warranty Claim Processing"
python -m afsgap run "Core and Exchange Unit Return"
```

If you have a process the business wants but nobody has built yet, run it too -
you get a blueprint instead of a gap analysis:
```bash
python -m afsgap run "Battery Pack Return" --new
```

## Sunday morning - review and tighten (about 2 hours)

1. Read every gap. For each one ask: *would I defend this in front of the process
   owner?* Where the answer is no, the fix is usually a better AS-IS description
   or a sharper search hint - both are re-runnable in minutes.
2. Check the exclusion log at the end of each document. Exclusions should look sensible; if the
   tolerance filter removed something you actually needed, that content belongs
   in a separate note outside this programme's scope.
3. Confirm every verdict has a rationale you agree with. The verdict is the part
   your stakeholders will quote.

## Sunday afternoon - package the deliverable (about 2 hours)

1. The DOCX files in `output/` are circulation-ready. Add your programme's cover
   page and logo.
2. Write a one-page cross-process summary: the verdict per process, the
   critical-severity gaps, and the quick wins. Everything you need is in the
   `_run.json` files.
3. Commit the process YAMLs (the analysis inputs are the asset worth keeping);
   `output/` and `.cache/` are gitignored by design.

## If you run short on time

Cut in this order:
1. Drop to two processes instead of three.
2. Use `--skip-industry` - SAP-versus-current alone still produces a defensible
   design document, and section 4 is then honestly marked as skipped.
3. Ship the Markdown and skip the DOCX packaging.

Do **not** cut: the AS-IS accuracy pass, or your own read of every gap. Those are
the two things the tool cannot do for you.
