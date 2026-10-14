# Design: Ingress — how knowledge earns its way into the graph

> **Implementation status (2026-08-07):** 🟨 **Core ingress gate built:** WorkItem,
> Decision, Verification, ExecutionPath, and Blocker provenance/validation shapes are
> present. Bootstrap proves both an untagged-write refusal and a falsifier-less
> Verification refusal. Execution-path inventory is now produced by the scheduled,
> ownership-neutral aegis source and the Quipu coverage query reports `Empty`,
> `Partial`, or `Full` rather than a bare count. Hank now emits a falsifier-gated
> `Verification` and a `built` `Blocker` for each signed unsatisfied verdict. Its
> source change is merged, but it is not yet an installed/runtime producer: the
> sanctioned installer consumes release artifacts and no verdict-drain schedule is
> present. Until both are true, the blocker query's zero is an explicit gap, not
> evidence that no blocker exists. **Rule 1 corrected (2026-08-16):** it claimed
> episode-shaped ingress; every real ingest is and always was a `/knot` write, so
> the rule now states the boundary it actually governs and `tests/test_knot_provenance.py`
> enforces the `actor` + `source` floor it narrowed to (camayoc-99t).
> **Planes update (2026-08-18, noted here 2026-08-22):** the §2 quarantine
> planes are no longer a table — `scripts/planes.py` registers and labels
> them (camayoc-s0h, see §2.1) and `scripts/promote_plane.py` implements the
> authority-gated plane promotion of §3.4/§4 (camayoc-mip) — assertion into
> the target only; the retraction half awaits a keeper ruling, `camayoc-913`.
> **Routing update (2026-08-24):** routing is now **2-D** — `plane_for(source_kind, data_kind="knowledge")`; `operational` data is window-addressed via `scripts/windows.py` so completed windows can be deep-frozen whole, and §4's promotion now CLOSES the source episode (the camayoc-913 move rule). Existing plane IRIs untouched. See [workflow-and-archive.md](workflow-and-archive.md).

## 1. The discipline, in five rules

1. **Episode-shaped writes for agent-recorded facts.** Everything the skill
   records in the moment — decisions, work items, outcomes — goes through
   quipu's `/episode` path, which supplies `prov:wasGeneratedBy` automatically
   and is idempotent (branch on `outcome`, never on `count`). That is where the
   provenance chain must be unskippable: those facts have an author, a moment
   and a motive, and a later reader has to be able to weigh them.

   **Bulk deterministic loads go through `POST /knot`, and this rule does not
   pretend otherwise.** The ontology and shapes (`scripts/bootstrap.sh`), the
   repository walk (`scripts/seed_knowledge.sh`) and the metric catalogue
   (`scripts/reconcile_metrics.sh`) are free-Turtle knot writes. They carry
   `actor` and `source` in the payload instead of a PROV activity;
   `prov:wasGeneratedBy` appears nowhere in the codebase outside these design
   docs.

   This rule previously read "every camayoc ingress goes through `/episode` …
   no free-Turtle writes to governed planes", which was false in every
   particular: the *only* `/episode` calls in the repository are the four
   deliberately-invalid gate probes in `scripts/gate_probe.sh`, and every
   successful ingest has always been a knot write (camayoc-99t).

   **What the narrowing costs.** `actor` + `source` is a weaker record than a
   PROV activity: it names who wrote and what they read, but not the run, and
   it is not chained, so a knot-written fact cannot be traced to the invocation
   that produced it. That is an acceptable trade for the ontology and shapes —
   schema, not claims — and a real gap for the seed walk and the metric
   catalogue, which *are* claims about a codebase. `tests/test_knot_provenance.py`
   holds the narrowed rule to its own terms: a knot write that names neither
   its actor nor its source fails the suite, so the weaker record cannot
   quietly become no record.
2. **SHACL refuses the untagged.** Every fact carries `camayoc:sourceKind`
   (`observed` | `declared` | `inferred`) and a source reference. Closed
   vocabulary, `minCount 1`, from the first load. This is NeuralAmplifier's
   proven posture (its ingest literally refuses a mod file claiming to be
   canonical), generalized.
3. **Deterministic-first.** If a parser can produce the fact from a record,
   the parser does — no model in the loop on `observed` facts, because a
   hallucinated fact is indistinguishable downstream from a real one on
   exactly the facts tagged trustworthy.
4. **Inference is quarantined, not banned.** LLM-derived knowledge (session
   summaries, "lessons learned," failure diagnoses) is valuable — it lands in
   its own plane (named graph) labelled low-trust in the lattice, so the
   label meets through every query that touches it. It can *earn* promotion
   (a graph move, bitemporal and auditable) — it can never masquerade.
5. **Facts true at write time.** No stored judgment that decays (liveness,
   currency, "still in progress"). Judgments are queries at read time.
6. **Verification names its falsifier.** A `Verification` carries the observable
   result that would have disproved it. The shape refuses a claim without one;
   bootstrap proves that refusal separately from the provenance-tag gate.
7. **Liveness is calculated, never written.** Producers store timestamped
   observations (a stop record, a refresh, an installed-artifact hash). A reader
   combines them with the current process or artifact state. `ExecutionPath`
   deliberately has no `isLive` property: a stored verdict would age into the
   stale fact it claims to detect.
8. **Blockers name their evidence strength.** A `Blocker` is exactly one of
   `stated` (a claim in a ticket/report) or `built` (a demonstrated failing
   case). When a built blocker links `demonstratedBy`, it must link a
   falsifier-gated `Verification`; Hank emits that link for signed unsatisfied
   verdicts. A reader can therefore keep a stated blocker visible without
   presenting it as a measurement. Absence of such a record remains a producer
   coverage question, not a claim that no blocker exists.

## 2. Planes by velocity

Mirroring the pattern NeuralAmplifier proved (datalinks / doctrine / memory),
mapped onto quipu named graphs with lattice labels:

| Plane (named graph) | Contents | Velocity | `sourceKind` | Trust label |
|---|---|---|---|---|
| `crew:records` | task lifecycle, decisions, outcomes (skill-recorded or parser-backfilled) | per-event | `observed` / `declared` | high |
| `crew:declared` | conventions, directives, standing decisions a human stated | occasional | `declared` | high (human root) |
| `crew:inferred` | model-written summaries, diagnoses, lessons | per-session | `inferred` | low, promotable |
| `code:*` | hank-promoted structure (already governed upstream) | per-commit | `observed` | per hank tier |

### 2.1 Implementation status: routed and labelled (camayoc-s0h)

> **Update 2026-08-18.** This section described a design. It now describes a
> deployment: `scripts/planes.py` registers each plane and labels it in the
> trust lattice, `scripts/bootstrap.sh` runs it, and writes are routed to the
> plane their `sourceKind` earns. The two quipu gaps below were closed upstream
> on the same branch — `POST /graph/create` and `POST /graph/label` expose
> `Store::graph_create` and `Store::set_graph_label`, both of which already
> existed with no way in from outside.
>
> **The ordering held.** Registration and labelling happen together or not at
> all, and `plane_for` refuses an unknown `sourceKind` rather than defaulting to
> ROOT. Against a quipu without the routes, bootstrap **fails** — it does not
> quietly carry on writing everything into ROOT, which is the "looks
> implemented, does nothing" state this section was written to warn about.
> `tests/test_planes.py` pins that refusal, and pins that `inferred` never
> shares a plane with `observed` and always ranks strictly below it.
>
> Writes go through `/episode`, not `/knot`, for the reason recorded below:
> `/knot` is hardcoded to ROOT and silently drops a `graph` key.
>
> The historical analysis is kept below because its reasoning is why the
> ordering is what it is.

**Nothing routed at the time of writing.** No script names a graph, no shape targets one, no query
carries a `GRAPH` clause. An `inferred`-tagged node lands in exactly the same
place an `observed` one does, so quarantine is skill discipline and a `sourceKind`
string — not an enforced boundary (camayoc-s0h).

Read against quipu's source rather than assumed, because the shape of the gap
decides who fixes it:

| Capability | State in quipu | Citation |
|---|---|---|
| Quad store, `GRAPH` / `FROM NAMED` in SPARQL | **exists** | `src/store/migrate.rs:33`, `src/sparql/pattern.rs:352-372` |
| Write into a named graph via `/episode` | **exists** (`graph` field) | `src/episode/mod.rs:177-181`, test at `src/episode/tests.rs:912` |
| Write into a named graph via `/knot` | **does not exist** | hardcoded to ROOT, `src/rdf.rs:180-182` |
| Trust lattice, labels, query-time floors | **exists** | `src/lattice.rs`, `src/store/labels.rs:730` |
| HTTP or CLI route to *set* a graph label | **does not exist** | only caller of `set_graph_label` is `src/pack.rs:450` |
| Labelling a graph `/episode` created | **refused** — the write interns the IRI without registering it | `src/store/labels.rs:344-350` vs `src/episode/mod.rs:371` |
| Store-level primitive that *registers* a graph | **exists, with no caller** | `Store::graph_create`, `src/store/overlays.rs:42` |

So routing is blocked in a specific and fixable way rather than generally:

1. **Every real camayoc ingest is a `/knot` write** (rule 1), and `/knot` cannot
   target a graph. Worse, it takes a free-form JSON body with no unknown-field
   rejection, so a `"graph"` key added to a knot payload is **silently dropped** —
   routing would appear to be implemented and do nothing. `tests/test_knot_provenance.py`
   pins that no knot payload carries one.
2. **Labels have no write route at all.** The whole lattice — composition,
   per-row annotation, query-time floors — is built and reachable only from
   Rust. A plane camayoc created could not be labelled `low-trust` even by hand.

**What unblocks this, in order.** Re-verified 2026-08-17, and the estimate is
smaller than it looks, because the missing pieces are *exposure* rather than
mechanism:

- `Store::graph_create` (`src/store/overlays.rs:42`) is **exactly** the
  registration `set_graph_label` presumes — written, tested, idempotent, and
  refusing a class change. It has **no caller outside its own tests**.
- `Store::set_graph_label` (`src/store/labels.rs:208`) is likewise built and
  reachable only from Rust, its single production caller being `src/pack.rs:450`.

So quipu does not need two mechanisms built; it needs `graph_create` invoked
when `/episode` is handed a `graph` it has not seen (or a route that does it),
and an HTTP route over `set_graph_label`. Camayoc then moves agent-recorded
facts to `/episode` with `graph` derived from `sourceKind` — and note that
`/episode`'s `graph` field **already works**, so camayoc's half is not blocked
on routing at all, only on the labels.

Until the label route exists, routing alone would produce separate graphs that
every query still reads at equal trust — the appearance of quarantine without
the substance, and worse than none. **That ordering, not the missing routing,
is why this is not partially shipped**, and it is worth being precise about:
camayoc could route today and must not.

This is a `built` blocker in the sense of §1.8, not a `stated` one: the
citations above are the demonstration.

## 3. Sources

### 3.1 The skill-guided agent — the first-class source

The primary ingress surface is **the agent itself**, guided by the shipped
skill ([skill.md](skill.md), `skills/camayoc/SKILL.md`): it records
decisions, work items and outcomes as tagged episodes at the moment they
happen. Guidance and enforcement are deliberately separate — the skill makes
the honest tag the path of least resistance; the shapes refuse what the
skill failed to prevent. **No harness is a dependency**: any agent with HTTP
reaches the whole surface.

### 3.2 Tracker and harness record parsers — optional enrichment

A tracker or harness with its own records can be backfilled and corroborated
by a deterministic parser translating records → episodes tagged `observed`:

**Implemented for beads/st: `scripts/ingest_work_items.py`.** It consumes the
public `br show <id> --json` record, reuses the existing `WorkItem` class, and
routes an ordinary governed episode to `crew:records`. The stable tracker id is
both the node name and `aegis:identifier`, so lookup works from record to graph
and from graph back to record. `--about <IRI>` records explicit WorkItem →
Directive/entity mappings without minting a tracker-specific class. Quipu owns
none of this translation: it only stores the episode Camayoc produces. Mutable
record versions are immutable `Observation` nodes keyed by a content digest;
the WorkItem projection is byte-identical on every run. A failed record is
printed as `outcome=parked` with exit 2 so batch callers can continue instead
of head-of-line blocking the lane.

Each tracker Observation also points `about` its WorkItem. The incoming
`WorkItem observes Observation` edge does not satisfy the Observation shape's
required outgoing `about` property. Fresh-bootstrap integration tests exercise
the complete installer, accept a real tracker episode, read the edge back from
its named graph, and reject an otherwise identical unanchored Observation.
Testing only the serializer or a store without write-time validation misses
this contract.

- **Beads (`br`)** is the cleanest such source: agent-first, JSON out, and a
  bead *is* a `WorkItem` record — id, status, dependencies, lifecycle — so
  the parse is nearly a projection. Where beads is st's tracker backend, one
  parser covers both.
- **Shantytown's own records** (task/crew, with the `ts`/`item`/`item_status`
  payload its event store already learned an event must carry) parse the
  same way. Shantytown's adapter table reserves a `knowledge` layer for a
  native emit half; that is a welcome *integration* on that repo's schedule
  — camayoc works identically without it, and its none-adapter proves the
  independence in the other direction.

### 3.3 Git

**Implemented: `scripts/ingest_git_provenance.py`.** Commit history →
work-item linkage (`aegis:implements`, `aegis:modifies` — the provenance chain
quipu's co-occurrence shapes already define). Pure `observed`; yupana promotes
the structural half.

Work-item targets use the bare tracker ID in the ontology namespace, matching
`ingest_work_items.py`. Provenance emits only the `implements` edge to that
WorkItem: its type, label, identifier and trust facts belong to tracker records
and are not copied here. Readers join the records and provenance graphs.
Code modules and commits retain their code namespace.
Older output used `code/bead/<id>` for work items; those historical aliases need
explicit reconciliation, not a second mint or deletion of their provenance.

```bash
scripts/ingest_git_provenance.py ../quipu ../yupana --project aegis > /tmp/prov.ttl
# then POST it to /knot like any other ingest — see scripts/bootstrap.sh
```

This chain is read by more consumers than any other thing this repo emits, and
none of them owns it: yupana's `WORK_ITEM_SCOPE_QUERY` builds the **observed
rung of its capability ladder** from it, its work-item briefing reads it for an
item's ground, and quipu's `entity_work` / `cochanged_with` / `cooccurrence`
traverse it. A repository this has never run against produces UNKNOWN scope for
every agent working in it — correctly, and uselessly.

**The project prefix must be declared** (`--project`, defaulting to the repo
names). There is no pattern that separates `bobbin-052` from ordinary
hyphenated English, and this was measured rather than reasoned: the first
version used a loose regex and read `work-item`, `advise-mode`, `authored-by`,
`pre-push` and `force-push` as work-item ids, writing paths into the ground of
five items that do not exist. A digit test does not rescue it either —
`bobbin-bbe` is a real id. So the ingest abstains unless somebody named the
project, and `tests/test_ingest_git_provenance.py` holds it to that.

**Coverage is reported, not assumed.** The runner prints how many commits
carried a recognised id and how many did not. A ground built from a history
where 80% of commits are unlinked is a *floor*, and whoever reads that scope
needs to know it — a linked-only count would let an ingest matching 5% of
commits look like one matching 95%.

### 3.4 Sessions (the inferred plane)

Transcript-derived summaries and diagnoses, written by a model, landing in
`crew:inferred` only. The interesting future here is the SARC trust-predicate
gap: a producer that records sub-agent responses is exactly what a session
adapter is — but v1 claims only quarantine, not evaluation.

### 3.5 Prose entity mentions (deterministic, still quarantined)

**Implemented: `scripts/extract_entities.py`** (camayoc-0c8). Markdown prose →
entity mentions, matched against a gazetteer of the graph's own labels plus
explicit user-declared regex patterns — no model in the loop. Deterministic
extraction does not make the claim `observed`: "this prose is about that
entity" is a reading, so every mention routes to `crew:inferred` and never
ROOT. Ambiguous labels match for nobody, abstentions are counted on stderr,
and an unreachable store or unprovisioned plane refuses before a triple
moves. See [entity-mentions.md](entity-mentions.md).

## 4. What ingress never does

- Never writes to a governed plane without the tags (SHACL enforces).
- Never upgrades its own output: promotion out of `crew:inferred` is a
  governed graph move requiring authority over the target plane, not an
  ingress feature. **Implemented as `scripts/promote_plane.py`**
  (camayoc-mip). Called *plane promotion* throughout, because
  `shapes/code-entities.ttl` already uses "promotion" for an unrelated
  bobbin-to-quipu ingest path and two governed operations sharing one word in
  one repo confuses every reader after the one who wrote it.
  Four gates, all refusing before anything is written: no authority over the
  target plane; **self-promotion**, checked independently of authority so a
  principal holding a grant still cannot launder its own output through it; a
  sideways or downward move, which is a category error that would read in the
  record as earned trust; and an unstated reason. Grants live in
  `config/plane-authority.json`, are human-maintained, and **fail closed** — a
  missing or malformed file grants nobody, because treating an unreadable
  grant file as unrestricted is what makes an authority check worthless.
  The promotion event is itself tagged `observed` and carries its own
  falsifier: the move is an observed event even though the fact it moves was
  inferred, and tagging it `inferred` would file the audit record in the plane
  it is moving things out of.
- Never stores a judgment that decays (§1.5).
- Never models the tracker's message format — the record is evidence about
  the work, not the shape of the work.

## 5. Related

- [bootstrap-ontology.md](bootstrap-ontology.md) — the vocabulary these
  writes carry.
- [skill.md](skill.md) — the skill-as-interface usage model this section
  implements.
- [task-lifecycle-slice.md](task-lifecycle-slice.md) — the first slice; its
  record mapping is the enrichment parser's translation table.
- shantytown `docs/adapters.md` — the reserved knowledge seam and the
  two-implementations rule; `shantytown/events.py` — the event-payload
  lessons §1.5 inherits.
- quipu `docs/design/graph-labels.md` — the lattice that makes quarantine
  enforceable rather than aspirational.

## Observed WorkItem consumer authority

The cost publisher reads deterministic tracker WorkItems from `crew:records`.
This is an explicitly granted read of observed evidence, not a move into ROOT
and not promotion of inferred facts. `publish_work_cost.py` requires the
`read:crew:records` capability under its own consumer name in
`config/plane-authority.json` before any graph access, including unchanged
snapshot runs. A missing, malformed, or unrelated grant refuses publication
and leaves the successful cursor intact. The actor argument cannot confer it.

The administrator authorized this grant on 2026-09-16 (aegis-trgavj). The
human-maintained file remains the authority; `CAMAYOC_AUTHORITY` selects an
operator-provisioned alternative, as for plane promotion. This is an
application policy gate, not an operating-system sandbox against a principal
that can modify the executable or its environment. Read capabilities never
satisfy the separate plane-promotion checks. Source facts remain in their
observed graph; the publisher still checks direct WorkItem typing before it
writes usage and verifies attribution in the same graph afterward.

### Standing tracker delivery

`scripts/sync_work_items.py` polls the public `br list --json` interface with
an explicit `--db`. Its scope is claimed work plus assigned open/blocked work,
including deferred assignments. It accepts the legacy array or a complete list
envelope; a truncated response is UNKNOWN. This is independent of cost records:
only the authoritative tracker can supply canonical WorkItem identity.

Each tick handles one record, at most one episode write and two verification
reads, with a persisted 60-second minimum interval and a 256-KiB episode cap.

A reviewed capacity trial can opt into at most four distinct records per eligible
tick without changing that default. The scheduler reads an optional policy beside
its state file: `state.json` uses `state.batch.json`. A policy carries integer
`max_items` (1–4), numeric epoch `starts_at` and `expires_at`, and a nonempty
`review` reference. Its window must be positive and no longer than one hour.
Missing, future or expired policies use one record; malformed policies report
UNKNOWN without making graph requests. The review field is an audit pointer,
not an authorization check: obtain operational review before installing a policy.
Installation of this code alone enables no trial.

The request ceiling is three per record and twelve per batch. A record occurs
only once in a batch, so controlled absent reads cannot become a same-tick retry.
The strict sixty-second gate, serialization, immutable pending bodies and fair
queue remain in force. Receipts carry aggregate counts, processed `items`, the
effective `max_items`, and the policy's expiry/review when present. BACKOFF is the
persisted time gate before graph requests; it does not itself measure writer
contention. Expiry or policy removal restores one record at the next admission,
without restart. An already-admitted bounded batch may finish.

Before activation, record a matched baseline and agree on latency, error and
scheduler-backoff stop thresholds. Use an isolated, explicitly coordinated
window; the earlier batch trial improved delivery but crossed its fleet latency
guardrail under mixed workloads. Do not retain a larger budget on throughput
alone. Expiry is a final bound, not a substitute for stopping a degraded trial.

Current claims win initial ties, then priority and newer creation time. Queue
ordering uses the later of first-seen time and last-attempt time. A new arrival
therefore cannot indefinitely jump ahead of an existing unverified transition,
and an attempted record rotates behind work already waiting. Initial backlog
drains gradually; consumers continue refusing absent
identities until delivery. This does not enlarge any consumer's request budget.

The existing mapper owns all semantics: stable WorkItem identity and an immutable
content-addressed Observation in `crew:records`, never ROOT. `--source` names the
tracker authority and the item ID is appended to it. Exact payloads are persisted
before writes. Delivery requires a positive same-plane WorkItem control and a
read-back of the exact direct WorkItem/Observation assertions. Neither `unchanged`
nor a nonzero transaction count proves delivery. Lost responses keep the payload;
two separately scheduled controlled absent reads permit a byte-identical retry,
with at most three write attempts. Further failures stay parked for inspection.
Changed tracker fields cannot replace an unresolved payload.

Confirmed versions receive a periodic six-hour read-back. Retraction is detected
and enters the same pending reconciliation path. Receipt and status metrics report
errors and backlog age; a backlog older than twice its bounded drain interval
(minimum fifteen minutes) is unhealthy. `--publish-status` emits independent
`camayoc_workitem_ingress_*` metrics, including timestamp and exit status, on every
run. Operators must arm status, stale and absent detection with the scheduler.
The scheduler should run this prerequisite before consumers; consumers retain
their own canonical-presence checks.

### Standing WorkItem recovery receipts

The standing tracker sync distinguishes `DEGRADED` from `UNKNOWN`:
verified delivery with retryable indeterminate entries is degraded (exit 0),
while a tick unable to verify pending work, invalid records, exhausted attempts,
or an over-age backlog remains unknown (exit 2). Healthy no-op ticks remain OK.
Receipts and metrics expose the indeterminate count independently of backlog.
Recovery receives priority on alternating ticks; the other ticks retain
least-recently-attempted fairness. This prevents both a fresh backlog delaying
all recovery reads and a broken control starving fresh work. Retry writes still
require two separately scheduled controlled absent reads and the original body.

`pending_oldest_seconds` measures time since a retained body entered recovery,
not time waiting in the delivery backlog. Its `started_at` is persisted before
the first write and survives reads, retries, tracker changes and restarts.
Backoff ticks recompute the age without making requests. Missing legacy clocks,
invalid clocks or a clock in the future report `-1` (unknown), never zero.
`exhausted_retries` counts pending bodies at the existing three-attempt limit;
invalid attempt counters also report `-1`. Both are exported with the
`camayoc_workitem_ingress_` prefix. Confirming every body clears both to zero.
These signals distinguish designed recovery from stuck delivery without changing
the write budget, controlled-read requirement or immutable retry payload.

### Quipu client credentials

Plane registration and governed RML writes share the credential order
`QUIPU_AUTH_TOKEN` (trimmed), `QUIPU_AUTH_TOKEN_FILE`, then
`~/.config/quipu/token`. An explicit in-process credential remains the caller's
override. An explicit missing file does not fall back to another credential;
the former legacy location must be selected explicitly.

Missing, invalid, unreadable or rejected credentials produce one diagnostic and
disable later writes for that server/session. A private marker under the Camayoc
state directory carries this state across hook processes when a harness session
ID is available. The diagnostic names process-only suppression when no session
ID exists or persistence fails. Public reads remain available. Obtain an accepted
credential from the service administrator, verify it with `caboodle doctor`, and
start a new session after repairing a refused credential.
