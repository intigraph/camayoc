# Design: Golden Paths — verified trajectories, blessed and enforced

> **Implementation status (2026-08-22, camayoc-gp1 + gp2):** 🟡 **Camayoc's
> slice is done to its deferral line: terms minted, gates armed, queries
> stored at 12/16.** The competency suite
> ([competency/golden-paths.md](https://github.com/intigraph/camayoc/blob/main/competency/golden-paths.md)) is written;
> the slice decided (see the §3 note) and the vocabulary is minted in
> `ontology/core.ttl` with shapes in `shapes/core.shapes.ttl` and three
> refusal arms in `scripts/gate_probe.sh` (proven able to fail by
> `tests/test_gate_probe.py`). Q1–Q13 minus Q5 run as named stored queries
> (`queries/camayoc_gp_*.json`) against a seeded fixture
> (`tests/fixtures/golden-paths.ttl`, positive + control-negative arms in
> `tests/test_golden_path_queries.py`; camayoc-gp2). The four gaps are
> blocked outside this repo, still, as of this date: **Q5** waits on quipu's
> `path cone` command (quipu-gp2, design-only — the term for per-step cone
> membership is deliberately unminted until that command's output shape
> decides it); **Q14–Q15** are deferred with the L5 ladder levels per the
> suite's acceptance note — `aegis:derivedConstraint` is minted but the
> Policy/grant/act records to join against only exist once quipu's
> verdict-signing-gated constraint derivation does; **Q16** waits on
> guard-written conformance records at scale (yupana FR-41, design-only —
> `aegis:deviatesAt` is minted and ready). Mechanisms remain design-only in
> quipu and yupana. Mechanism designs live with their owners:
> [quipu docs/design/golden-paths-blessing.md](https://github.com/scbrown/quipu/blob/main/docs/design/golden-paths-blessing.md)
> (storage, pruning aid, backtest, promotion) and
> [yupana docs/golden-path-guard.md](https://github.com/scbrown/yupana/blob/main/docs/golden-path-guard.md)
> (conformance guard, plan pre-check).

## 1. The arc

An agent picks up a work item and uses the graph to understand it. When a
work item completes with **verified results**, its trajectory — the ordered
steps that actually got it done — becomes raw material. Trajectories that
earn it are **pruned** (steps that were correct but didn't serve the goal are
cut, with humans ruling on anything the deterministic analysis can't) and
**promoted** into **golden paths**: blessed trajectories that guide similar
future work. At the far end of the ladder, a blessed path backs **authz
constraints** — an agent is permitted to act *because* its plan instantiates
a path that demonstrably worked.

Blessing is earned two ways, and only two:

- **Deterministic verification.** Falsifier-gated Verifications on the
  result, provenance-cone analysis on the steps, backtests over recorded
  history. Small, checkable, mechanical — the kernel that untrusted work must
  satisfy. (This is the honest extent of the Lean 4 parallel: a tiny trusted
  checker, untrusted generators producing artifacts it can refuse. Nothing
  here is theorem proving; everything here is refusable evidence.)
- **Human promotion.** Every level transition is a human act, recorded as a
  fact with provenance. The machinery narrows what a human must look at; it
  never replaces the looking.

## 2. Reuse before minting

The slice reuses settled vocabulary first; camayoc mints only what no owner
has.

| Existing term | Owner | Reused for |
|---|---|---|
| `aegis:WorkItem`, `aegis:outcome`, `aegis:closedAt` | camayoc core (slice 1) | the unit of work and its close disposition |
| `aegis:Decision` (`chose`, `over`, `rationale`, `decidedBy`) | camayoc core (slice 1) | pruning rulings and promotion rulings |
| `aegis:Verification` + `aegis:falsifier` | camayoc core (slice 2) | the "verified results" admissibility gate |
| `aegis:supersededBy` | camayoc core (slice 1) | path demotion/replacement, never deletion |
| `aegis:sourceKind` | camayoc core (ingress) | observed trajectory vs declared intent vs inferred similarity |
| `aegis:exemplar` | quipu governance (`shapes/governance.ttl`) | a path citing the concrete trajectories it was distilled from |
| `aegis:Policy`, `aegis:effect`, `aegis:OperatingPoint` | quipu governance | the constraint a blessed path eventually backs |
| `prov:` activity/derivation | W3C PROV-O (quipu episodes) | step ordering and the provenance cone |

## 3. Candidate terms (the slice decides)

Each candidate is owed to a competency question (cited). All are minted in
the store's base namespace per the core.ttl namespace note, `rdfs:range`
only, never `rdfs:domain`.

> **Slice decisions at minting (camayoc-gp1):** omissions and promotions are
> reified — `omitsStep` points at a **`PathOmission`** node (`omittedStep` +
> mandatory `omissionAuthority` of `cone-analysis|human-decision`, optional
> `omissionRuling` → Decision), and the promotion properties live on a
> **`PathPromotion`** event node (`promotes`, `blessingLevel`, `promotedBy`,
> `promotedAt`, `promotionEvidence`) — so each ruling carries its authority
> without waiting for statement-level attachment. And the required
> provenance of a GoldenPath is **`prunedFrom`** (an in-graph exemplar
> Trajectory); `aegis:exemplar` stays available, optional, for exemplars
> living outside the graph, matching quipu's governance use.

| Candidate | Kind | Owed to | Meaning |
|---|---|---|---|
| `Trajectory` | class | Q1, Q2 | The observed, immutable record of the steps a work item actually took. Never edited after the fact; pruning creates a new entity, it never touches this one. |
| `Step` | class | Q1, Q4, Q5 | One action in a trajectory: actor, action kind, target, the Decision it enacted, the Verification it produced (if any), order. |
| `GoldenPath` | class | Q6–Q16 | A pruned, promoted trajectory class. Cites its exemplar Trajectories via `aegis:exemplar`; carries its blessing history. |
| `stepOf` / `stepOrder` | property | Q1 | Step membership and total order within a Trajectory. |
| `enacts` / `verifiedBy` | property | Q1, Q4 | Step → Decision it enacts; Step (or terminal claim) → Verification. `verifiedBy` ranges over `aegis:Verification`, so the falsifier discipline applies unchanged. |
| `prunedFrom` | property | Q5, Q6 | GoldenPath → exemplar Trajectory it was distilled from (alongside `aegis:exemplar`, which may point outside the graph). |
| `omitsStep` | property | Q6, Q7 | GoldenPath → Step it deliberately excludes, each omission carrying its authority: cone-analysis or a human Decision. |
| `deadEnd` | property | Q7 | GoldenPath → an abandoned branch preserved as a hazard: "exemplars tried this; it did not help." Negative knowledge, kept queryable. |
| `blessingLevel` | property | Q8 | The level set at promotion time — a fact of the promotion event, like `aegis:outcome` at close. Never a stored decaying judgment of current fitness (Q16 is answered at read time). |
| `promotedBy` / `promotedAt` / `promotionEvidence` | property | Q8, Q10 | The human act and its cited evidence (backtest run, verification set). |
| `followsPath` | property | Q11, Q12 | A live trajectory's declared claim to be an instance of a GoldenPath. A claim, not a verdict — conformance is evaluated, not asserted. |
| `deviatesAt` | property | Q12 | Where an evaluated trajectory departs from its declared path: the step and the manner. |
| `derivedConstraint` | property | Q14, Q15 | GoldenPath → the `aegis:Policy` it backs, closing the audit chain constraint ← path ← exemplars. |

## 4. The blessing ladder

Multiple levels, because "correct" is not one thing. Every transition upward
is a recorded human promotion sitting on a deterministic admission gate;
every level can be left, downward, by supersession.

| Level | Name | Deterministic gate (machine-refusable) | Human act |
|---|---|---|---|
| L0 | recorded | the trajectory parsed from real records (`sourceKind observed`) | none — everything is recorded |
| L1 | verified | work item closed `done` AND terminal claim carries falsifier-gated Verifications | none — admissibility is mechanical (Q3) |
| L2 | candidate | pruning complete (every omission carries cone-analysis or a Decision); backtest run and recorded (Q13) | the pruning rulings on in-cone steps |
| L3 | advisory | projected and served warn-tier; conformance recorded, nothing blocked | promotion to advisory, citing the backtest |
| L4 | blessed | advisory period elapsed with conformance evidence accumulated | promotion to blessed; deviation now warns loudly, deny opt-in |
| L5 | constraint-backing | verdict signing exists (see the yupana addendum's Phase-4 caveat — honestly far-horizon) | promotion of the derived `aegis:Policy` through the existing advisory→enforcing gates |

The ladder deliberately maps onto quipu's existing effect ladder
(`warn` → enforcing) and its promotion gates — golden paths do not get a
second, parallel promotion machinery.

## 5. Pruning semantics

A successful trajectory usually contains steps that were *correct* but did
not serve the goal — exploration, a branch that was walked back, scaffolding.
Pruning is how a trajectory becomes a path, and it has one rule: **the raw
Trajectory is immutable**. Pruning authors a new GoldenPath that cites what
it keeps and records what it omits.

The deterministic aid: compute the **provenance cone** of the verified result
— the transitive derivation closure from the terminal Verifications back
through the steps' outputs. A step outside the cone contributed nothing the
verified result depends on: **mechanically prunable**, no human needed
(Q5). A step inside the cone is load-bearing: pruning it requires a human
`aegis:Decision` with rationale (Q6), because the cone says it mattered and a
human is overruling the cone.

Pruned branches that represent genuine exploration are preserved as
`deadEnd` hazards (Q7): a follower of the path should know what the
exemplars tried that didn't help. Pruning is curation, never erasure —
bitemporality keeps everything anyway; this just keeps it *meaningful*.

## 6. Mechanism assignment

Camayoc owns what these facts mean and how they earn their way in. The
machinery lives with its owners:

| Mechanism | Owner | Reuses |
|---|---|---|
| Trajectory ingestion (episodes, PROV) | quipu | `/episode` path, auto-provenance |
| Provenance-cone computation | quipu | `Store::speculate` / impact machinery |
| Path backtest over history | quipu | `src/governance/backtest.rs` pattern, generalized from single-edit exemplars to trajectories |
| Candidate drafting, born advisory | quipu | `quipu policy draft` scaffold; policy-by-example step 2, generalized |
| Promotion / demotion gates | quipu | existing advisory→enforcing gates, `aegis:supersededBy` |
| Path projection to the guard | yupana | quipu→yupana policy-projection path, projection freshness served |
| Conformance guard (warn / opt-in deny) | yupana | FR-30 pre-edit hook + FR-35 game-state guard, generalized to steps |
| Plan pre-check (what-if over a step sequence) | yupana | `yupana_impact` / `whatif` |
| Named stored queries for this suite | quipu | the quipu #79 pattern |

This is [policy-by-example](https://github.com/scbrown/quipu/blob/main/docs/design/policy-by-example.md)
generalized from **point exemplars** (one observed edit) to **trajectory
exemplars** (one observed success): the same four-step gesture — point,
draft, backtest, born-advisory — applied to a sequence instead of a hunk.

## 7. Related work

The blessing ladder's nearest published relative is **ActiveGraph**
(Nakajima, *The Log is the Agent: Event-Sourced Reactive Graphs for
Auditable, Forkable Agentic Systems*,
[arXiv:2605.21997](https://arxiv.org/abs/2605.21997)): an event-sourced
agent runtime whose fork→test→promote loop is this design's promotion arc
with the same auditability motive — every mutation traces to its events,
runs replay deterministically, and hypotheses are cheap forks. The
convergence is real and worth citing; the difference is the evidence bar.
ActiveGraph's lineage records *what happened*; this design additionally
demands what happened **earn** its standing — falsifier-gated verification
before admissibility, cone-checked pruning, a backtest before a rule is
born, and a recorded human act before anything enforces. Its
fork-at-any-event ergonomics are worth adopting on quipu's speculate
machinery (filed as quipu-gp5), with one constraint stated up front: a
promoted fork re-enters through the write gates, so fork convenience never
becomes a gate bypass.

## 8. What this slice refuses to do

- **No stored "still good".** A path's current fitness (Q16) is a read-time
  judgment over conformance evidence; only promotion *events* are stored.
- **No model-asserted blessing.** Similarity suggestions and drafted
  candidates may be model-produced, but they land as inferred-plane facts,
  tagged, and nothing above L1 happens without a recorded human act.
- **No second promotion machinery.** Levels ride quipu's existing gates.
- **No L5 before signing.** Constraint-backing authorization waits on signed
  verdicts; until then the ladder honestly stops at L4.
