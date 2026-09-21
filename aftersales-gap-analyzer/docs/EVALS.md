# The eval set

## What problem this solves

Everything else in this project tests *mechanics*: that a filter fired, that a
quote matched, that a document rendered. None of it answers the question that
matters when you change a prompt or swap a model:

> Did that make the analysis better or worse?

The eval set answers it.

## The idea: the ground truth is already written down

Hand-authoring "the correct answer" for an SAP process would mean asserting SAP
facts - exactly what this tool refuses to do without a source. So most
expectations are **derived from your own process definitions** instead:

* every entry in `known_pain_points` must be addressed by at least one gap;
* every step-level pain point should be addressed (valued, not blocking);
* every `custom_object` must be given an explicit S/4HANA disposition.

That asserts nothing about SAP. It only asks whether the analysis engaged with
what the business told it. One process definition yields about twenty
expectations with no authoring at all, and they stay true as you correct the
process description.

On top of that, each case may carry a few **hand-authored** expectations for
things that cannot be derived, and a list of **forbidden statements**.

## Three scores, never averaged

| Family | Question | Target |
| --- | --- | --- |
| **Compliance** | Did the output obey the rules? No tolerance content, no unverified transaction code, industry content KPI-free, document complete | **100%.** Anything less is a defect, not a lower score |
| **Grounding** | Are findings tied to retrieved, allowlisted evidence? Quotes present in the pages they cite, SAP sources official | High, and falling is a warning |
| **Insight** | Does it find the problems the business already documented, and are its recommendations specific? | Directional - this is the number you try to move |

Blending these into one figure would let fluent prose hide a compliance breach,
so they are always reported separately, alongside a count of **blocking
failures** (a `must` expectation that failed).

## Running it

```bash
# free, deterministic, no model and no network - run this on every change
python -m afsgap.evals run --offline --tags offline

# the full set against whatever backend you are using
python -m afsgap.evals run --llm ollama --label "qwen-7b-baseline"

# re-grade runs you already have in output/ - costs nothing
python -m afsgap.evals score

# did the change help?
python -m afsgap.evals compare evals/results/<before>.json evals/results/<after>.json

# what is in the set, and what each case expects
python -m afsgap.evals cases
```

Exit code 2 means at least one blocking failure, so the offline subset drops
straight into CI.

## Graders

**`--grader keywords`** (default) needs no model, costs nothing and is perfectly
reproducible. It asks whether the distinctive words of an expectation appear in
the analysis. It over-credits - a document can say "inspection" without
addressing the inspection problem - so treat it as a floor. Use it on every
change.

**`--grader claude`** / **`--grader ollama`** asks a model whether each
expectation was genuinely addressed, **and requires it to quote the sentence
that does so**. The quote is then checked against the document: a verdict the
judge cannot support with a real quote is downgraded rather than believed. That
turns the judge's answer into something falsifiable. If the judge fails, the run
falls back to keywords rather than failing.

## The set

Ten cases: four gap-mode against real process definitions, two greenfield, and
four deterministic guard cases that run offline for free.

The guard cases exist because the fixtures deliberately contain a tolerance
statement, a KPI, and two unverifiable transaction codes. If the pipeline ever
stops filtering them, those cases fail immediately, at no cost.

## Making it yours

1. **Correct the process definitions.** The eval is only as good as the pain
   points in `data/processes/*.yaml`. `data/processes/underdelivery.yaml` is a
   template - replace it with the real thing.
2. **Run the offline set, then a live baseline**, and label it
   (`--label qwen-7b-2026-09`).
3. **Add a case per process you care about.** A case is a dozen lines of YAML;
   most of its expectations come from the process definition.
4. **Add forbidden statements** as you discover things the tool should never
   say for your programme.
5. **Compare before shipping any prompt or model change.**

## Honest limits

* The keyword grader is generous. A rising insight score under it is weak
  evidence; a falling one is strong evidence.
* Ten cases is a small sample. Treat a change of a few points as noise, and
  a change in blocking failures as real.
* Insight scores are not comparable across different process sets - adding a
  process with many pain points will move the aggregate on its own.
* The judge shares a family of biases with the model being judged if you use
  the same one for both. Grading Claude output with Claude flatters it; the
  quote requirement limits but does not remove that.
