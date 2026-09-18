# Methodology - how to defend this in a design review

## The question the tool answers

> Is our process best in the market, or can it be improved - and specifically
> how, given we are moving to S/4HANA?

It answers it by triangulating three views of the same process: what we do
(AS-IS), what SAP documents as standard, and what leading players do. A gap is
where these three disagree, and the recommendation is how to close it.

## What "evidence" means here

| Claim type | Admissible source | Rejected |
| --- | --- | --- |
| SAP process step, module, T-code | Official public SAP domains only | Consultancy blogs, forums, training sites, model recall |
| Industry practice | Consultancies, standards and industry bodies, research institutes, recognised industry press, peer-reviewed work | Vendor marketing, forums, content farms, model recall |
| Current process | The process owner's own description | Anything else |

Every source used is listed in section 8; every source rejected by the
credibility filter is listed in 8.3. A reviewer can re-open any of them.

## Deliberate exclusions, and why they are defensible

**Tolerances** are out of programme scope, so they are excluded end to end
rather than mentioned and set aside. This avoids an entire class of review
distraction, and the exclusion log in section 9 proves nothing was quietly
dropped.

**KPIs in the industry benchmark.** Published benchmark numbers are the least
durable and least verifiable part of any practice study: they are rarely
comparable across companies, they age quickly, and they invite argument about the
number instead of the practice. The benchmark therefore describes *how leaders
run the process*, which is what a design decision actually needs. Your own
KPI baseline stays where it belongs - in the programme's business case.

**Unverified T-codes.** A transaction code is the single easiest thing for a
language model to produce plausibly and wrongly, and the single most embarrassing
thing to get wrong in front of a functional team. Codes are therefore verified
against the literal text of a public SAP page or not published at all. Section
3.3 lists what was rejected and why, so "we found nothing" is distinguishable
from "we did not look".

## Known limitations - state these before someone else does

* **Public sources only.** SAP material behind a customer login, and your own
  configuration, are not visible to the tool. Open questions in section 3.7 are
  where the functional team should pick up.
* **Research is a point in time.** Re-run before a milestone; the run date is on
  the document.
* **The verdict is a judgement, not a measurement.** It is grounded in the cited
  sources, and `insufficient_evidence` is an honest and available answer.
* **AS-IS accuracy is your responsibility.** The gap analysis is only as good as
  the process description fed in.
* **The tool does not size a business case.** It produces effort bands (S/M/L/XL)
  and dispositions, not cost.

## How to challenge a finding

Every gap cites its evidence. To challenge one: open the cited source. If the
source does not support the claim, that is a defect - record the URL and the
claim, and tighten the relevant prompt or filter. If the source supports it but
your reality differs, the AS-IS input needs correcting and the process re-run.
