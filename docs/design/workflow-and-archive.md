# Design: Workflow runs and the archive — the second routing dimension

> **Implementation status (2026-08-25):** ✅ **Built, question-first, and
> now answering.**
> [competency/workflow-and-archive.md](https://github.com/intigraph/camayoc/blob/main/competency/workflow-and-archive.md)
> landed before any term; the workflow slice
> (`aegis:WorkflowDefinition/WorkflowStep/WorkflowRun/TransitionEvent` and
> their properties) is minted in `ontology/core.ttl` with each term owed to a
> question. Routing is 2-D: `plane_for(source_kind, data_kind="knowledge")`
> in `scripts/planes.py` (the default preserves every pre-2-D caller because
> every static plane IS a knowledge plane — a pinned fact, not a fallback),
> and `operational` data is window-addressed via `scripts/windows.py`
> (`{WINDOW_NS}{family}/{YYYY-MM}`, create-and-label-or-neither, the scheme
> pinned by `tests/test_planes_2d.py` against shuttle's reimplementation).
> The camayoc-913 move rule is decided and implemented in
> `scripts/promote_plane.py`: assert in the target, CLOSE the source episode
> (`POST /episode/retract`, a bitemporal close, `on_orphan: preserve`),
> record the move; without `--source-episode` the record carries
> `camayoc:sourceLeftOpen true` out loud. Existing plane IRIs are untouched
> (bobbin compiles them in). Deliberately not built here: the freeze
> mechanics (quipu's, see its `graph-kinds-and-deep-freeze.md`) and shuttle
> itself (`scbrown/shuttle`).
>
> **The questions run (2026-08-25, camayoc-rkb).** Eight of the fourteen are
> named stored queries — `queries/camayoc_wf_*.json`, Q1–Q5, Q7, Q8 and Q13 —
> executing against `tests/fixtures/workflow-archive.trig`, a TriG dataset
> holding a hot window, a frozen window, the definitions plane and the
> identity graph. 36 tests in `tests/test_workflow_queries.py`; Q13 replays
> against the episode `scripts/promote_plane.py` actually emits rather than
> against fixture triples written to match it. Coverage: **8/14**, six gaps,
> nothing unwritten. Q9–Q12 and Q14 are the store-surface boundary — graph
> kinds, freeze state and thaw records are meta-graph and store-table
> properties served by `GET /graphs`, not triples a query can reach — and Q6
> is the crypto boundary: SPARQL fetches a public key and cannot evaluate a
> signature against it. No term was minted to close a gap.

**Status:** shuttle exports workflow runs — high-volume, high-churn
operational records — into quipu. This is the ingress discipline for that
class of data: what it means, where it lands, and how it ages out.

## 1. The second dimension

Ingress routing was 1-D: `sourceKind` → plane. The planes answer *how much
to trust a fact*; they say nothing about *what sort of data it is*. Workflow
runs are `observed` facts, but routing them into `crew:records` would grow
the knowledge plane without bound and make nothing freezable — the graph is
quipu's unit of pack/attach/label/authority, and freezing `crew:records`
would archive the crew's task lifecycle along with the runs.

So the router takes `(source_kind, data_kind)`:

- `knowledge` (default): the static planes, exactly as before;
- `operational`: **refused** by `plane_for`, pointing at the window scheme —
  one graph per producer-family per month, labelled
  `operational`/`fresh`/`soleRecord` at creation, frozen whole when its runs
  are terminal (the freeze relabels to `archive`/`backed`);
- anything else: refused, never ROOT.

## 2. The move rule (camayoc-913, closed)

Promotion and freeze are both graph moves, and both now follow one rule:
**assert in the target, close in the source, record the move.** The close is
bitemporal — quipu closes the valid interval, never deletes — so the
original stays visible as-of its write time (competency 13). The worry that
kept 913 open was that closing erases the fact's low-trust past;
bitemporality answers it.

The promotion close operates on the **source episode**, because the episode
is the unit ingress writes and `POST /episode/retract` is the graph-honest
retraction surface (triple-level `/retract` is ROOT-scoped). Commit order is
assert-then-close: a failed close leaves the fact readable in two planes at
different trust — visible, recoverable, and reported loudly — while
close-then-failed-assert would lose the promoted fact.

## 3. What shuttle writes (the export contract)

Append-only `aegis:TransitionEvent`s (step, from-state, to-state, time,
`aegis:performedBy` + `aegis:signature` — the hex ed25519 signature over the
canonical `shuttle-transition-v1` message), with `aegis:currentState`
re-asserted per transition as derived convenience. Keys live as
`aegis:VerifierRegistration` facts in a `dataKind=identity` graph that is
never frozen, so signatures in a frozen window stay verifiable. The
signature terms are quipu-owned aegis vocabulary, reused not re-minted.

## 4. Related

- quipu `docs/design/graph-kinds-and-deep-freeze.md` — the store mechanics.
- [ingress.md](ingress.md) §2 — the planes this extends.
- [what-belongs-in-the-graph.md](what-belongs-in-the-graph.md) §4b — the
  durability lattice freeze declares against; §5 — why freeze is post-hoc
  relocation, never write-time filtering.
- `scbrown/shuttle` — the producer.
