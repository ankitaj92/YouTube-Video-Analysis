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
| Current process | The organisation's own Confluence page, transcribed without additions | Anything else, including what "should" be there |
| "This process does not exist" | The published search record: terms, spaces, candidates and their scores | An unevidenced assertion |

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

## What "tolerance topics are excluded" actually means

The exclusion targets **tolerance configuration**: tolerance keys, groups,
limits and profiles, and the over/under-delivery allowances built on them. It
does not target the business events.

That distinction matters because they share vocabulary:

| Statement | Treatment |
| --- | --- |
| "The dealer reports an underdelivery of three units" | Kept - a business event |
| "Underdelivery is investigated by the claims team" | Kept - a process step |
| "The under-delivery tolerance is checked before posting" | Excluded - configuration |
| "Over-delivery is permitted up to the configured limit" | Excluded - configuration |
| "Maintain the tolerance key per plant" | Excluded - configuration |

Over- and under-delivery terms are therefore **contextual**: excluded only when
the same sentence is also about tolerances, limits, allowances or thresholds.
Context is judged sentence by sentence, so a "limit" in a roadmap paragraph
cannot make an unrelated sentence look like tolerance configuration.

**Any process can be analysed.** "Underdelivery" and "Over-delivery Handling"
are real aftersales processes, and users search using their own landscape's
vocabulary. If a process name does overlap the exclusion vocabulary - say
"Tolerance Management" - the run proceeds, the name is allowed through so the
document can be titled, and the overlap is noted in the validation log. A
programme that prefers such requests stopped at the door can set
`AFSGAP_REFUSE_EXCLUDED_PROCESS=true`; it is off by default.

If findings for such a process look thin, read the exclusion log at the end of
the document: it lists every removed segment, so you can judge whether
`tolerance_terms.yaml` is drawing the line in the right place for your
programme. It is meant to be edited.

## Known limitations - state these before someone else does

* **Public sources only.** SAP material behind a customer login, and your own
  configuration, are not visible to the tool. Open questions in section 3.7 are
  where the functional team should pick up.
* **Research is a point in time.** Re-run before a milestone; the run date is on
  the document.
* **The verdict is a judgement, not a measurement.** It is grounded in the cited
  sources, and `insufficient_evidence` is an honest and available answer.
* **AS-IS accuracy is your responsibility.** The gap analysis is only as good as
  the Confluence page it transcribes. A page that is out of date produces an
  analysis of a process nobody runs any more - which is why the page version and
  last-modified date are printed in section 2.
* **"Not found" means not found in the searched spaces.** A process documented
  in a space the account cannot read, or under a title with no words in common
  with the process name, will be treated as new. Section 2 shows exactly what was
  searched so this is checkable in seconds.
* **If the run used a local model, say so.** The sourcing guarantees are
  identical, but synthesis quality is not. The run record carries the backend,
  and the exclusion log shows how often the model had to be corrected - a long
  list of post-generation warnings is a signal to re-run hosted before the
  document goes to a steering committee.
* **A greenfield blueprint is a proposal, not a validated design.** It is
  anchored in SAP standard and leading practice, but it has not been through your
  functional team, your data, or your dealer network. Its design decisions are
  written to be argued with.
* **The tool does not size a business case.** It produces effort bands (S/M/L/XL)
  and dispositions, not cost.

## How to challenge a finding

Every gap cites its evidence. To challenge one: open the cited source. If the
source does not support the claim, that is a defect - record the URL and the
claim, and tighten the relevant prompt or filter. If the source supports it but
your reality differs, the AS-IS input needs correcting and the process re-run.
