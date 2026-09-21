# Eval results

Each run writes `<timestamp>_<label>.json` here. Keep the ones that matter -
a baseline before a change and the run after it - and compare them:

```bash
python -m afsgap.evals compare evals/results/<before>.json evals/results/<after>.json
```

Committing a baseline is worthwhile: it is the record of what the tool could do
on a given day, with a given model and prompt set.
