# Finishing this weekend

The code is complete and tested offline. What remains is *your* content: real
process definitions, one live run per process, and a review of what comes back.

## Saturday morning - setup and first live run (about 2 hours)

1. Follow `docs/PYCHARM_SETUP.md` (20 min).
2. Run the offline configuration. Read the generated document in `output/` end
   to end - it shows you exactly what the deliverable looks like (20 min).
3. Do a **single live run** on Defective Parts Return (10 min of runtime):
   ```bash
   python -m afsgap run data/processes/defective_parts_return.yaml -v
   ```
4. Read section 3 (SAP standard) and section 8.4 (queries run) critically:
   * Are the retrieved SAP pages the ones you would have opened yourself?
   * Did any T-code get rejected that you know is real? That is a research
     problem (the right page was not retrieved), not a verification bug - add a
     sharper query to `sap_area_hints.yaml` and re-run that stage.
   * Is section 4 genuinely qualitative?

## Saturday afternoon - make the AS-IS real (about 3 hours)

The single biggest quality lever is the accuracy of the AS-IS input. Nothing the
tool does can compensate for a vague process description.

For each process, fill in: every step, who does it, in which system, what the
step produces, and the pain points. Name the custom ECC objects - the gap
analysis uses them directly to produce retire/keep recommendations.

Budget roughly 45 minutes per process with the process owner. Do
`defective_parts_return` first, since it is already drafted and just needs
correcting against reality.

## Saturday evening - the other processes (about 1 hour)

Run each process once. Cache means re-running the report is free:
```bash
python -m afsgap run data/processes/dealer_warranty_claim.yaml
python -m afsgap run data/processes/core_exchange_return.yaml
```

## Sunday morning - review and tighten (about 2 hours)

1. Read every gap. For each one ask: *would I defend this in front of the process
   owner?* Where the answer is no, the fix is usually a better AS-IS description
   or a sharper search hint - both are re-runnable in minutes.
2. Check section 9 of each document. Exclusions should look sensible; if the
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
