# The 2026-08-06/07 incident corpus

> **Implementation status (2026-08-25):** 🟡 **Labelled; the denominator is
> now instrumented, and unmeasured until a window runs through it.** The instances and their taxonomy are
> recorded contemporaneously and are solid. The **rate** the paper needs is
> still *not* computable from the record of 2026-08-06/07 as it was kept, and
> §4 says so explicitly rather than estimating one — that is a property of a
> past window and no later work changes it. What changed is §5, the
> prospective half:
>
> - **Items 1–2 are no longer blocked outside this repo.** The 2026-08-22
>   audit said "a refused write never enters the graph, so that count lives
>   with quipu's refusal path". It does, and quipu built it: main `71440ff`
>   records every gate refusal (`shacl|policy|authority|owl|placement`) as a
>   durable `write.refused` event carrying graph/actor/source/reason/
>   `refused_datums`, served by `GET /events?types=write.refused` and counted
>   by `quipu events refusals`. **A queryable denominator source exists.**
>   **Item 1 is built:** `scripts/refusal_denominator.py` (`just
>   refusal-rate`) is that query, joined to the accepted `Verification`
>   population and needing no new camayoc term. It reports and never writes —
>   a rate between two moving populations is a read-time judgment, not a fact
>   true at write time. Building it corrected this section twice: `reason`
>   names the GATE, not the failing shape, so "refused for a missing
>   falsifier" is an upper bound rather than a count; and the refusal stream
>   is prospective while the accepted population is the whole resident graph,
>   so the share is a floor for that reason as well as for `speculate`. What
>   is still unmeasured is a real window: the instrument exists, nothing has
>   run through it yet, and a number will only mean something once one has.
> - **Item 2 stays deferred, for a different and correct reason.** An A1–A7
>   controlled vocabulary on refused verifications is now *buildable* and
>   still **must not be minted**: no competency question asks for per-form
>   refusal counts, and competency-before-classes is not suspended because a
>   dependency cleared. The question comes first or the term does not come at
>   all. This is a deliberate hold, not an unbuilt item.
> - **Item 3 is built.** `scripts/ingest_session_usage.py` (`just
>   ingest-usage`) parses a harness's own session logs into the §D vocabulary
>   `camayoc-e29` minted, so Q16–Q21 run against a live window instead of only
>   against the fixture. Measured while building it: the unit of consumption
>   is the API request, not the log entry — see §5.
>
> Tracked as `camayoc-0d3` rather than left as prose.
> (§4.2's own lesson bit this repo once more meanwhile: the coverage tool's
> verification denominator was itself undercounted until 2026-08-22 — six §D
> questions were missing from it, and four of the suite's six SLICES — 56
> questions — until 2026-08-25, when it stopped being a lesson anyone restates
> and became a test.)

Source of record: [`../../competency/verification-and-liveness.md`](https://github.com/intigraph/camayoc/blob/main/competency/verification-and-liveness.md),
written during and immediately after the night of **2026-08-06/07** on a live
crew. This document turns that prose into a labelled corpus with the taxonomy
as categories, per `camayoc-101`.

## 1. Why this is the paper's strongest asset

Papers about verification integrity are usually arguments. This one has
instances: **eleven separate checks that could not have failed**, observed in
one night, on a running multi-agent crew, recorded by the people who were
fooled by them rather than reconstructed afterwards by someone looking for
examples.

The unifying observation, and the sentence the paper should be built on:

> The night's dominant failure was not a wrong answer. It was **a check
> performed against something adjacent to the claim, reported as verification,
> by careful people.**

"By careful people" is load-bearing. Nothing here is explained by negligence,
which is what makes it a systems problem and therefore an admission-gate
problem.

## 2. Category A — verification integrity

Seven recurring forms. Each was observed at least once; the eleven instances
distribute across these seven forms (per-form counts are **not** recoverable —
see §4.2).

| ID | Form | What was checked | What was claimed | The adjacency |
|----|------|------------------|------------------|---------------|
| **A1** | Redundancy that wasn't | One guard present and working | "Belt and braces — there's a second mechanism" | The documented backup did not exist. The single point of failure was documented *as* a redundancy. |
| **A2** | Right test, wrong mode | Byte identity of a file | "The file is unchanged on disk" | The comparison ran against a **read-write open**, so it compared the buffer to itself. |
| **A3** | Partial-key comparison | Schema `(type, name)` | "The schema matches" | `ADD COLUMN` changes neither type nor name of existing columns, so the whole class of additive migration was invisible to the check. |
| **A4** | Branches that don't branch | A binding condition with two arms | "Both paths are covered" | Both arms called **the same function**. The conditional was decorative. |
| **A5** | Matching the description, not the thing | `grep -c` over a test payload | "The bug signature is present/absent" | The payload contained a *description* of the bug. The grep matched the prose, not the behaviour. |
| **A6** | Zero-from-nothing | `grep` over a capture | "Absent — count is 0" | The capture was **empty**. A `0` from an empty input and a `0` from a populated one are different facts; the check could not tell them apart. |
| **A7** | Corroboration that doesn't discriminate | Evidence consistent with hypothesis H1 | "This confirms H1" | The same evidence was **equally consistent with H2**. It had no discriminating power between the live hypotheses. |

**The falsifier test applied to each.** For every row, ask the constraint
camayoc enforces at ingress — *what result would have proved this wrong, and
could the check have produced that result?*

- A1, A4: no. The check's output was independent of the property claimed.
- A2, A6: no, **given the conditions on the night**. Under other conditions
  (read-only open; non-empty capture) the same check is sound. This is the
  distinction competency question #5 preserves deliberately: a check in an
  adjacent setting is valid exactly when the mechanism under test does not
  depend on the variable that changed. These two are not bad checks; they are
  sound checks run outside their preconditions, which the falsifier field would
  have surfaced by forcing the precondition to be named.
- A3: partially — it could fail for a renamed or retyped column, never for an
  added one. **A falsifier scoped narrower than the claim.** The most insidious
  form, because the check does work sometimes.
- A5, A7: no. Both could return "confirmed" under the negation of the claim.

**This is the paper's argument in miniature and it should be presented exactly
this way.** Not "these were bad checks" — a reader nods and moves on — but
"here are seven checks, and the single question `what would have falsified
this?` separates all seven from verification, mechanically, without hindsight."
A3 in particular is where the mandatory `falsifier` field earns its keep: a
human writing "renamed or retyped column" into the field has, in that act,
noticed that `ADD COLUMN` is not covered.

## 3. Category B — held-work liveness, and Category C — armed ≠ merged

**B: four mechanisms, one missing predicate.** Four P1s open simultaneously,
one root cause: *every routing mechanism recorded an owner without checking the
owner was alive.*

| Mechanism | Recorded | Never checked |
|---|---|---|
| Bead assignment | assignee | assignee running? |
| Alert keeper labels | keeper | keeper running? |
| `BLOCKED` status | blocker | blocker still real? |
| `in_progress` status | holder | holder running? |

Consequence identical in all four, and worst-possible: *a thing correctly
detected and correctly recorded is indistinguishable, from every dashboard,
from a thing that was never detected at all.*

Measured durations: a P1 blocked **14 days** on a dependency that had already
cleared; a retraction that never propagated for **14 days**; a CI break unread
for **6 hours across 12 production deploys**.

The sharpest instance is recursive and should be quoted verbatim in the paper:
**the bead documenting invisible in-progress work was itself sitting
in-progress, invisible, on a stopped agent.**

**C: armed ≠ merged ≠ correct.** A guard correct in git and absent from every
execution path for **24h**; a config feature landed, tested, and inert until
armed; a rules file **six weeks** stale reaching agents while the current one
sat in the repo; **17** host execution paths running scripts out of individual
working trees.

B and C are the same defect at different layers: a fact recorded at write time
that silently decays. They are why ingress rule 5 refuses to store decaying
judgments at all, and why liveness is a read-time join rather than a stored
predicate.

## 4. The two attacks, answered

`camayoc-101` names both, and they are the right attacks.

### 4.1 Selection bias: were these found by looking for them?

**Partly, and the corpus must say so.**

Honest reconstruction of how the instances surfaced:

- **The Category B family was not sought.** Four P1s were open simultaneously
  and the common cause was noticed *because* they collided. This is closer to a
  census than a search: the population is "P1s open that night", and the
  discovery was of a shared cause, not of instances of a pattern.
- **The Category A instances were partly sought.** Once A1 was found — a
  documented redundancy that did not exist — the crew began asking the falsifier
  question of other checks, and found more. **The first instance was
  incidental; later ones were elicited by a search.**

This matters and cuts a specific way. A corpus assembled by searching for a
pattern demonstrates that the pattern is *findable*, not that it is *common*.
So the corpus supports:

> ✅ "When we asked what would have falsified each check, checks failed that
> question at a rate that surprised us, across seven distinct forms."

and does **not** support:

> ❌ "N% of verifications in this codebase are unfalsifiable."

The paper must make exactly the first claim. The second requires a denominator
we do not have.

### 4.2 Count versus rate: eleven out of what?

**The denominator is not recoverable from the record as kept.** Stating this
plainly is the only defensible option, and it is consistent with the paper's own
thesis: a claim must name what would falsify it, and "eleven instances" names
nothing.

What *is* anchored:

| Quantity | Value | Confidence |
|---|---|---|
| Instances of unfalsifiable verification | 11 | Recorded contemporaneously |
| Distinct forms | 7 | Recorded |
| Window | one night, 2026-08-06/07 | Recorded |
| Simultaneous P1s sharing one root cause | 4 | Recorded |
| Routing mechanisms missing the liveness predicate | 4 of 4 examined | Recorded — **this one is a rate** |

What is **not** anchored, and must not be estimated:

- **Total verifications performed that night.** Not counted. Without it there is
  no "11 out of N".
- **Per-form counts.** Eleven instances across seven forms; the distribution was
  not recorded.
- **Crew size and agent-hours.** Not captured in the record, so no per-agent or
  per-hour normalisation.

**The one genuine rate in the corpus is Category B: 4 of 4.** Every routing
mechanism examined lacked the liveness predicate — a complete census of a small,
enumerable population, with no selection step, because the four were identified
by their collision rather than by a search. That is the number the paper should
lead with when a rate is demanded, and it should be presented for exactly what
it is: a small denominator, completely enumerated.

## 5. What would fix this for the next window

Concrete instrumentation, so the gap is a finding with a remedy rather than an
apology:

1. **Count the denominator at ingress.** ✅ *Built (2026-08-25).* Once
   `Verification` is a shape-gated class, every accepted verification is a
   stored fact — that half has been true since aspect 4. The missing half was
   the refusals, and a refused write never enters the graph, so for a year the
   honest statement here was "this count lives somewhere camayoc cannot
   reach". It now lives somewhere camayoc can *query*: quipu records each gate
   refusal as a durable `write.refused` event with graph/actor/source/reason/
   `refused_datums`, served by `GET /events?types=write.refused`. Total
   accepted, total refused for a missing falsifier, and the ratio are a join
   away, and the refusal count is itself the rate this corpus lacks — measured
   prospectively, with no search step. Two boundaries the event stream states
   about itself and this document must repeat rather than smooth over:
   refusals inside `speculate` are excluded, and the refused **fact bodies**
   are deliberately not stored, so the stream answers *how many and why*, not
   *what was rejected*. A per-form count therefore cannot be recovered from
   the refusal record alone — which is what item 2 is about.

   `scripts/refusal_denominator.py` (`just refusal-rate`) is that join. It
   **reports and never writes**: a refusal rate is a ratio between two
   populations that both keep moving, which makes it a judgment computed at
   read time rather than a fact true at write time, and storing it would
   store something that starts decaying with the next write. 37 tests in
   `tests/test_refusal_denominator.py`, most of them abstentions — a store
   that could not be reached, an event envelope in an unrecognised shape, and
   an `/ask` answer carrying no count all exit 3 rather than rendering as
   zero refusals, which is `gate_probe.sh`'s distinction applied to the same
   failure one layer up.

   **Two corrections this section owes to having built the thing.** First,
   *"total refused for a missing falsifier" is not a join away and this
   document said it was.* The stream's `reason` field names the GATE that
   refused — `shacl | policy | authority | owl | placement` — not the shape
   that failed, and the bodies are gone. So the shacl total is an **upper
   bound** on the missing-falsifier count and there is no path from this
   source to the count itself. The reporter prints it as an upper bound and
   names no narrower quantity; any narrowing is an operator-supplied
   `--reason-contains` filter, reported as the operator's classification.
   Second, *the two populations do not share a time origin,* which is §4.2's
   own defect at a new scale: refusals are a prospective stream that began
   when quipu started recording them, while accepted `Verification`s are the
   resident graph — every write ever made, the pre-gate legacy population
   included. The denominator is therefore too large and the share too small.
   That makes the reported share a floor in three independent ways, and the
   report states all three on every run rather than in a footnote.
2. **Record the form.** ⛔ *Deliberately not built, and not for want of a
   dependency.* A controlled vocabulary over A1–A7 on refused verifications
   would give per-form counts directly. **No competency question asks for
   them.** Camayoc's first convention is competency-questions-before-classes —
   no ontology term without a question in `competency/` that needs it — and
   that rule does not lapse because the blocking dependency cleared. Minting
   A1–A7 now would be minting a taxonomy this document happens to contain,
   for a query nobody has written, which is exactly the failure the rule
   exists to prevent. **The question comes first, then the terms.** Until
   someone writes it, §4.2's missing per-form distribution stays missing and
   stays stated. Item 1 landing does not change this: the reporter built for
   it deliberately names no per-form quantity, and its own test suite asserts
   that no refusal-form vocabulary exists in `ontology/core.ttl` — so a
   future session reaching for the terms trips a test before it reaches the
   ontology.
3. **Normalise by agent-hours.** ✅ *Built (2026-08-25).* Both harnesses
   already write complete per-session token accounting to local disk, so this
   is a deterministic-parser fact (ingress rule 3) and needed no new plumbing.
   `scripts/ingest_session_usage.py` (`just ingest-usage`) is that parser: it
   emits `aegis:Session` and `aegis:UsageRecord` in the vocabulary
   `camayoc-e29` minted, tagged `observed`, so Q16–Q21 run against a live
   window rather than only against the fixture.

   **Two findings from building it, both of which the format description
   would not have told anyone.** First, *the unit of consumption is the API
   request, not the log entry*: the claude harness writes one entry per
   content block and repeats the whole turn's `usage` object on every one of
   them. Measured against a real session — 237 entries carrying usage, 92
   distinct requests, the usage object identical across each group — the
   naive per-entry sum reports 39,467,766 tokens for a session that consumed
   15,653,391. A 2.5x overcount, in the direction that flatters a throughput
   claim and inflates a spend one, and it would have looked entirely
   plausible. Second, *only the claude reader is verified*, because no real
   codex rollout file was available to measure one against; codex logs are
   counted as unrecognised in the run's denominator and emit nothing, since
   a parser written from a prose description of a format is a guess, and the
   first finding is precisely what such a description omits.

Note the shape of this: **the mechanism the paper proposes is also the
instrument that would have measured the problem it was built for.** That is
worth one sentence in the paper and no more — it is a genuine property of the
design, and overselling it would be its own unfalsifiable claim.

## 6. How to use this corpus in the paper

- **Lead with A1–A7 and the falsifier question applied to each.** It is
  concrete, it is mechanical, and it requires no hindsight.
- **Use B's 4-of-4 as the rate**, with the small enumerated denominator stated.
- **Put §4 in the paper, not in an appendix.** A corpus that names its own
  selection bias and its own missing denominator is more credible than one that
  does not, and this paper in particular cannot argue that claims must be
  falsifiable while making an unfalsifiable one about its own evidence.
- **State that the LIVENESS corpus motivates the mechanism rather than
  validating it.** As of later on 2026-08-22 the scope of that sentence
  narrowed: the cost/denominator family (§D, Q16–21) and the section-A
  single-edge questions (Q3/Q4/Q5/Q7/Q12) run as stored queries
  (`camayoc-89e`, `camayoc-e29`; 16/19 on that slice, up from 5/19 that
  morning — the "4 of 47" this bullet cited on 2026-08-17 predates the
  golden-paths slice's 12/16). What still does not exist is the
  liveness-join family (#6, #8, #9) — the four-beads-one-cause questions
  themselves — which remain GAP rows in `just query-coverage`; see
  [`implemented-set.md`](implemented-set.md) §2. For section B the corpus
  is still the *problem statement*, with the evaluation to follow.
