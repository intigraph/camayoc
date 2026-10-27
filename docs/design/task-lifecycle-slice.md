# Design: First Slice — task lifecycle + decisions

> **Implementation status (2026-10-17):** 🟡 **The spine is built and proven
> end-to-end**: `ontology/core.ttl` + `shapes/core.shapes.ttl` ship (v1,
> deliverable 1), the skill and plugin ship, and the loop was verified
> against a live quipu with `validate_on_write` — bare store → bootstrap →
> untagged probe **refused** → tagged Decision accepted → competency-style
> query answers → session hook reports ACTIVE. Since the 2026-08-06 banner,
> deliverable 5's substance landed in `just test` rather than `just check`:
> the stored queries execute against seeded fixture graphs with positive and
> control-negative arms (`tests/test_competency_queries.py`,
> `tests/test_golden_path_queries.py`), and `just query-coverage` reports
> per-question coverage with gaps named. Deliverable 4's stored-query
> loading and serving path ships:
> `scripts/bootstrap.sh` POSTs definitions from `queries/` to `/queries`,
> and Quipu lists and runs them through `/ask`. Counts and gaps come from
> `just query-coverage`; loading still has to succeed on each deployment.
> Real-record answer acceptance below remains separate from API availability.
> Remaining: the crew-domain vocabulary beyond core (deliverable 2 — no
> `ontology/crew.ttl` exists; later slices minted into core instead) and the
> optional shantytown-records enrichment parser (deliverable 6 — unbuilt; the
> deterministic parser that does exist, `scripts/ingest_git_provenance.py`,
> covers git provenance, a different record source). No term is minted until
> a mapping row or competency question needs it.

## 1. Why this slice

"What did we decide about X, and why" is the highest-value question a crew's
memory can answer, and the task lifecycle is the provenance spine every
decision hangs from. It is also the slice with the most honest data source:
shantytown's task/crew records exist today, are deterministic to parse, and
their event architecture has already learned exactly which facts an event can
carry truthfully.

## 2. Two paths into the graph — skill first, parser as enrichment

**The spine is the skill-guided agent** ([skill.md](skill.md)): decisions,
work items and outcomes recorded as tagged episodes at the moment they
happen, by any agent in any harness. That path needs nothing built beyond
the ontology, the shapes, and the skill this repo already ships.

**The enrichment path** is a deterministic parser over a harness's records —
shantytown's are the worked example below — backfilling and corroborating
the skill-recorded spine with `observed`-tier facts. It is optional, and no
harness is a dependency.

### The record mapping (enrichment parser, st as the worked example)

One episode per record batch, all facts tagged `sourceKind: observed`, into
the `crew:records` plane. Candidate mapping (the implementation refines
against real records; the *shape* is the commitment):

| St record | Episode facts |
|---|---|
| task created (`st task "…"` → `st-1`) | `crew:st-1 a camayoc:WorkItem ; rdfs:label "…" ; camayoc:createdAt <ts>` |
| task assigned / inboxed to agent | `crew:st-1 camayoc:assignedTo <agent-iri>` (the agent is the existing `aegis:Principal`) |
| agent stop with item held | `camayoc:workedOn` occurrence with `ts`, `item_status` — exactly the payload st's event store records, no more (no liveness verdicts: facts true at write time) |
| task done / closed | `crew:st-1 camayoc:outcome camayoc:done ; camayoc:closedAt <ts>` |
| decision recorded | `camayoc:Decision` node: `decidedIn crew:st-1 ; decidedBy <principal> ; chose "…" ; over "…" ; rationale "…"` |

Decisions are the one place the slice needs a record st does not currently
write: a **decision record**. Two paths, both kept open: (a) a lightweight st
convention (a `decision:` line in task notes the parser lifts), and (b) the
session adapter writing decisions into `crew:inferred` until a human or
convention promotes them. Start with (a) — declared/observed beats inferred.

## 3. Competency questions = acceptance tests

The slice's question set lives in
[competency/crew-task-lifecycle.md](https://github.com/intigraph/camayoc/blob/main/competency/crew-task-lifecycle.md)
and doubles as the test suite: the slice is **done** when each question runs
as a named stored query through the implemented `/ask` API against a graph
built from *real*
shantytown records, and an agent (or Stiwi) accepts the answers as faithful.
That definition-of-done is stolen deliberately from the claims planning doc:
model with sign-off, real historical data validated, questions running as
named queries, and a trace a reviewer accepts.

## 4. Deliverables

1. `ontology/core.ttl` + `shapes/core.shapes.ttl` — the bootstrap terms the
   slice actually needs (and no more).
2. `ontology/crew.ttl` + `shapes/crew.shapes.ttl` — the crew domain.
3. **The skill, exercised end-to-end**: `skills/camayoc/SKILL.md` ships
   already; the slice proves it — a skill-guided agent bootstraps a bare
   store, records a real session's decisions/work/outcomes, and the
   competency questions answer from what it recorded.
4. The competency queries as stored queries: bootstrap loads `queries/`
   through `/queries`, and `/ask` serves them. Raw-SPARQL patterns remain
   available in the skill. Pack delivery and acceptance on real records are
   separate from this implemented loading path.
5. An eval gate: `just test` runs the questions against a fixture graph —
   built from skill-recorded episodes, optionally corroborated by the
   enrichment parser.
6. *(Optional, unblocking nothing)* `camayoc crew ingest <records-root>` —
   the deterministic record→episode enrichment parser, st as the first
   worked example.

## 5. Scope boundaries (honest)

- **No task↔code linkage in this slice** (`aegis:modifies` joins come with
  the git adapter, next slice) — the decision spine must stand alone first.
- **No failure-memory** ("has this been seen before") — needs the inferred
  plane and its promotion story; deliberately after the observed spine.
- **No harness dependency, period.** The spine is skill + HTTP. The
  enrichment parser reads records from outside; any harness-native emitter
  (st's reserved `knowledge` seam) is that harness's integration, on its
  schedule.
- **Nothing here claims real-time.** Ingest is batch/poll; the crew's live
  coordination stays in st where it belongs.
