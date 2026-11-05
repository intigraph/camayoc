# Competency questions — verification integrity and held-work liveness

The test suite for a second slice, proposed from a measured incident night on a
live crew (2026-08-06/07): **eleven separate instances of a check that could not
have failed**, and **four separate beads describing one root cause** — work
recorded correctly and routed to a principal who was not running.

Same discipline as slice 1: every term must be owed to a question here, and
every question must eventually run as a named stored query (quipu #79).
Parameters in angle brackets.

## A. Verification integrity — "could this check have failed?"

The night's dominant failure was not a wrong answer. It was a **check performed
against something adjacent to the claim**, reported as verification, by careful
people. Measured forms, each at least once:

- a guard documented as belt-and-braces that was the *only* mechanism
- a byte-identity test that passed against a read-write open
- a schema check comparing `(type, name)` only, so `ADD COLUMN` was invisible
- a binding condition whose two branches were the same function
- a `grep -c` that matched the *description* of the bug in the test payload
- a `grep` against an empty capture, whose `0` was read as "absent"
- a "corroboration" equally consistent with both competing hypotheses

Questions:

1. What is the falsifier for `<verification>` — what result would have proved
   it wrong, and could the check have produced that result?
2. Which verifications in `<area>` carry no falsifier? (These are assertions
   wearing a verification's clothes.)
3. Which claims of the form "X is fixed/landed/deployed" have a falsifier that
   was never re-run after `<change>`?
4. Which checks were proven adversarial — i.e. a deliberate sabotage was shown
   to flip them — and which were only asserted to work?
5. For `<check>`, does the mechanism under test depend on the variable that was
   changed? (A test in an "adjacent" setting is valid exactly when it does not;
   this distinction is the counterpart to Q1 and prevents discarding good
   evidence.)

**Shape obligation.** `camayoc:falsifier`, `sh:minCount 1`, on any
`Verification` — the same posture `sourceKind` already takes on `WorkItem` and
`Decision`, and for the same stated reason: *the tag is the reader's only signal
of trust*. A verification without a falsifier is refused at ingress rather than
stored and believed. Bootstrap proves both refusal arms independently: an
untagged Decision and an otherwise valid Verification missing only its falsifier.

## B. Held-work liveness — the four-beads-one-cause family

Four P1s were open simultaneously describing one root cause: **every routing
mechanism recorded an owner without checking the owner was alive.** Bead
assignment, alert keeper labels, `BLOCKED` status, `in_progress` status — four
mechanisms, one missing predicate. Consequence in each case identical and worst
possible: *a thing correctly detected and correctly recorded is indistinguishable,
from every dashboard, from a thing that was never detected at all.*

Measured: a P1 blocked 14 days on a dependency that had already cleared; a
retraction that never propagated for 14 days; a CI break unread for 6 hours
across 12 production deploys; and — the sharpest — the bead *documenting*
invisible in-progress work, sitting in-progress, invisible, on a stopped agent.

Questions:

6. What work items are held by a principal that is not currently running?
7. What work items are `<blocked>` on a dependency that is already closed?
8. Which alerts/escalations are keyed to a principal that is not currently
   running?
9. For `<work-item>`, how long has it been held without a state transition, and
   by whom?
10. Which blockers are *stated* (asserted by whoever wrote the ticket) versus
    *built* (demonstrated by constructing the failing case)? A stated blocker is
    a claim; a built case is a measurement, and the two were confused three
    times in one night.

**Why these are queries and never stored facts.** Ingress rule 5 already
settles it: *"No stored judgment that decays (liveness, currency, 'still in
progress'). Judgments are queries at read time."* Liveness is the canonical
decaying judgment. The st stop payload (`frm`, `item`, `item_status`, `ts`) is
the observed record — slice 1 Q12 already claims it — and "is that principal
alive *now*" must be evaluated at read time or it becomes the very staleness it
was meant to detect.

## C. Armed ≠ merged ≠ correct

A third class, distinct from both: code that is committed, merged, and still not
running — or running from a copy nobody tracks.

Measured: a guard correct in git and absent from every execution path for 24h;
a config feature landed, tested, and inert until armed; a rules file six weeks
stale reaching agents while the current one sat in the repo; and 17 host
execution paths running scripts out of individual working trees.

Questions:

11. For `<mechanism>`, what is the execution path — which artifact actually
    runs, from where, refreshed by what, and when was it last refreshed?
12. Which mechanisms differ between their repository source and their installed
    artifact?
13. Which execution paths resolve into a location owned by a single principal
    (rather than a shared, refreshed, read-only checkout)?

## Acceptance

The slice is done when 1–13 run as named queries against a fixture graph, and
when Q2 and Q6 each return a **non-empty** result on a deliberately seeded
fixture — a query that cannot return findings has the same defect as the checks
in section A, one level up.

### Ingress obligations already enforceable

- A `Verification` without `falsifier` is refused (M1).
- An `ExecutionPath` without its executed artifact, repository source, or
  refresh mechanism is refused (M3). The resulting facts support Q11–Q13;
  they do not store a liveness verdict.
- A `Blocker` without exactly one `blockerEvidence` value (`stated` or
  `built`) is refused. When a producer supplies `demonstratedBy`, it must be a
  falsifier-gated Verification (M4). This keeps Q10's claim/measurement
  distinction in the data while yupana/quipu own the producer-side construction
  proof.

The remaining retrieval work is to load Q6–Q13 as Quipu stored queries and to
have st/yupana emit their observed records. Each query needs a seeded positive
finding and a control negative; an empty result without those arms is not proof
that the fleet is healthy.

## D. Cost and effort accounting — what did the work actually take?

Added 2026-08-07. A crew running more than one agent program has no way to answer
what any of it cost, and the gap is not the provider's fault: **both harnesses
already write complete token accounting to local disk**, per session, with no API
and no credentials.

    codex   ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl
            input · cached_input · cache_write · output · reasoning_output · total
    claude  ~/.claude/projects/<slug>/<session>.jsonl
            input · cache_creation · cache_read · output · ephemeral_1h/5m

These are **deterministic-parser facts** — ingress rule 3 applies directly: a
parser can produce them from a record, so a parser does, and they enter as
`observed` with no model in the loop.

The distinction that makes this tractable, learned by getting it wrong: **remaining
quota and consumed effort are different quantities from different sources.** A
provider may publish neither, one, or both. Consumption is ours; the ceiling is
theirs. Conflating them cost this crew a closed investigation and a governor half
that was declared impossible.

Questions:

16. What did `<work-item>` cost, in tokens, across every principal that touched it?
    Break this down by harness, request model and disjoint uncached input,
    cache-read input, cache-write input and output; unknown fields stay absent.
17. What has `<principal>` consumed in `<time-window>`, by provider?
18. What is the work-per-token of `<principal>` or `<provider>` — items closed,
    or decisions recorded, per unit of consumption?
19. Which sessions carry NO usage record? (These must read as UNKNOWN, never as
    zero; a missing measurement that aggregates as 0 is wrong in the flattering
    direction, and three of seven local sessions had none.)
20. At `<time>`, what was the burn RATE per provider — and is that answerable
    without ever knowing the ceiling? (It is: rate needs consumption alone, which
    is why the pace question survives a provider that publishes no quota.)
21. What did `<decision>` cost to reach — i.e. consumption attributable to the
    work items in which it was made?

**Why this belongs in the graph rather than a dashboard.** A dashboard answers
17 and stops. Questions 16, 18 and 21 are joins — consumption against work items,
decisions and outcomes that this ontology already models. Cost becomes a property
of *work*, not of a time-window, which is the only form in which it can inform
what to build next.

**Acceptance.** Q16 and Q18 must run against a fixture where the expected answer
is known independently — a session whose printed token count is compared against
the parsed one. A usage reader checkable only against itself is the section-A
defect wearing an accountant's hat.
