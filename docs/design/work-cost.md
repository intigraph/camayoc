# Request cost attribution

**Status: implemented locally; scheduled delivery and live acceptance pending.**

Q16 now asks for per-work model and disjoint token counts. Camayoc owns the
arithmetic. Shantytown supplies versioned focus intervals containing the rig,
bead, agent, session, start, end and boundary evidence. Neither global assignment
nor a session-wide bead guess is allocation evidence.

`retrieve_metric.py` accepts the `session_usage` system and `work_cost` query,
with `params.sources` (path, harness, agent) and `params.bindings`. It parses
Claude request identities and Codex response identities, including compaction.
Identical repeated rows count once; conflicting rows abstain. Missing models or
counts stay unknown. Reasoning and cache TTL details are retained as metadata.

Intervals are half-open. A request matching no interval or conflicting intervals
remains unattributed. Repeated identical bindings do not double-charge a request.
Totals describe the supplied sources, never an unmeasured lifetime cost.

`publish_work_cost.py` uses that same parser and replaces only its own session
snapshot in the observed plane. Request IRIs retain the existing usage producer's
identity. The existing `inSession` and `attributedTo` edges join requests to
sessions and canonical WorkItems. The tracker producer must have populated those
WorkItems first. No separate bead twins or new session predicate are introduced.

The optional breakdown shapes travel with the producer's write. They extend
UsageRecord; no new entity class is needed. Reads store no metric sample. USD is
a read-time projection over the versioned, cited price catalogue described below;
it is never stored as observed token evidence.

Rollback disables the scheduled projection and retains evidence. It does not
rewrite tracker close reasons or silently delete prior cost observations.

## Cost metric source groups

Status: source-aware publisher implemented; coordinated rollout proof pending.

Active-source exposition can carry a `# st-bead-cost-source` JSON comment with
the parser's agent, harness and session identity. `publish_cost_metrics.py`
validates that identity before pushing anything. It sends token and coverage
samples to a Pushgateway group keyed by rig/agent/harness/session, and scheduler
status to the existing rig-only group. Rotation preserves earlier sessions;
corrected attribution replaces only the affected session's allocations.

Identity and counts travel in one atomically replaced exposition file, avoiding
a crash mismatch between separate count and identity files. Cached review and
cooldown refreshes retain the source identity and original observation time.
`st_bead_cost_source_last_success_timestamp_seconds` reports that source's
observation age; the rig heartbeat does not establish fresh cost observations.
Unmarked payloads retain the legacy rig-only publication contract.

Install this publisher before enabling source markers. Each marked metric
publication makes two gateway PUTs, one sample group and one status group;
this does not change graph requests, snapshot limits, or token arithmetic.
Both PUTs must succeed for the caller to report success. The separate groups
are not a gateway transaction; a partial failure is reported for reconciliation.
Totals remain scoped to observed supplied sessions, not unmeasured lifetime cost.

## First-rollout load budget

Each invocation permits at most one changed session snapshot, eight distinct
attributed WorkItems, 1,000 requests and 4 MiB of serialized snapshot body.
Exceeding any limit refuses before network access; partition backfills explicitly.
Unchanged snapshots issue no request. The changed snapshot makes at most three
requests: one bounded VALUES SELECT preserving an asserted-only FILTER, one
snapshot write, one read-back. Starts are spaced at least one second apart.
The initial schedule must be no more frequent than once per minute. Every cost
request carries `X-Quipu-Client: camayoc-cost`; other plane helper callers use
`camayoc-ingress` or `camayoc-planes`. Raising these limits requires a reviewed load budget.

Read-back checks the sampled request's exact attribution set as well as its total,
so a corrected focus retaining an old WorkItem edge leaves the pending marker
unresolved instead of reporting success. This samples one request, not the entire
snapshot; rollout acceptance must include a deliberately re-attributed request.

## Resilience gate and recovery

The caller label is required: cost sends `camayoc-cost`, tracker ingress sends
`camayoc-ingress`, and plane/window administration sends `camayoc-planes`.
Preflight uses one bounded VALUES SELECT and asserted-type FILTER. A partial
result names missing IDs. Zero rows trigger one single-item control and return
INDETERMINATE, never an instruction to repeat ingress. A 408 or transport timeout
aborts before writing.

Every publication attempt writes a sibling `.receipt.json` and `.prom` next to
its state, and emits the receipt to stderr. Receipts distinguish OK, UNKNOWN,
STALLED and OPERATOR_ACTION, list observed items per changed snapshot, and name
read-back as a last-record sample. The scheduled wrapper must surface nonzero
exit to the coordinator and retain stderr; an unmonitored cron logfile is not
an acceptable schedule. The status metric file is available for a textfile
collector, but its existence alone does not establish a scrape.

Calls are spaced one second apart and capped at three per attempt. Persisted
receipts enforce at least sixty seconds between network-bearing attempts. A
write taking over ten seconds, or summed request wall time over fifteen seconds,
sets a fifteen-minute cooldown. Request wall time is a conservative budget
proxy; it is not measured server lock occupancy. Cooldown survives restart.

A pending marker means **the write may have landed**. It contains the exact
canonical JSON body sent on the wire. Automatic runs preserve it and report
STALLED; they never retry that write. Franklin (or the coordinator's explicitly
assigned recovery operator) owns reconciliation:

1. Stop the scheduled cost publisher while retaining the marker, state, receipt
   and source inputs. Record their SHA-256 values on the task.
2. Inspect the stored Turtle for the snapshot identity and request facts. Query
   one previously known canonical WorkItem using the asserted-type FILTER as a
   control. If the control fails or reads time out, leave the marker in place.
3. Read the marked snapshot's expected request totals and attribution sets,
   including the corrected assignment if this was a correction. Repeat after a
   gap. An extra attribution edge is a mismatch, not success. A sample is only a
   sample; record precisely which requests were checked.
4. If reads prove the expected snapshot, write its SHA-256 (of the marker bytes)
   under the marker's `snapshot` key in the local state atomically, then remove
   the marker. This is the same cursor/digest format as normal publication.
   Retain the evidence on the task before resuming.
5. If two controlled reads show absence, a recovery operator may replay the
   **unchanged marker bytes** to `/knot` with `X-Quipu-Client: camayoc-cost` and
   write authorization. Respect the recorded cooldown and at most three
   requests per minute. Do not regenerate it from a growing transcript or edit
   its wording. Acceptance still requires the controlled read-back in step 3;
   another timeout leaves the same marker and the schedule stopped.

Narrow sources when a snapshot exceeds eight items, one thousand requests or
four MiB; do not silently drop records to fit. Report the first scheduled run's
actual items-per-snapshot distribution before asking to enlarge that budget.

### Twenty-shape budget review

Receipts always carry `items_per_snapshot`, `records_per_snapshot` and
`body_bytes_per_snapshot`; empty lists mean no snapshot dimensions were measured,
not a zero-sized measured snapshot. During cooldown the prior dimensions are
retained with `dimensions_source: previous_attempt`. A budget refusal emits
`camayoc_cost_projection_stalled{reason="budget"} 1`; pending writes use
`reason="pending"`, transport/preflight failures use `reason="error"`, and normal
cooldown uses `reason="backoff"` with value zero. `projection_ok` distinguishes
intentional cooldown from a successful attempt.

The sibling `.budget.json` retains maximum observed items, records and bytes,
including budget refusals. Its run count advances only when a changed snapshot
reaches canonical preflight. Repeated runs of one shape do not provide a
distribution: the review becomes due only after twenty distinct
`(items, records, body_bytes)` tuples have reached preflight.
`camayoc_cost_preflight_runs` retains the run count;
`camayoc_cost_distinct_shapes` exposes distinct progress and
`camayoc_cost_budget_review_due` reports the twenty-shape gate.
The reviewer re-rules from the **maximum**, never the median.
A hand-selected two-item pilot proves one input only and does not discharge this
review. Limits remain enforced until an explicit new budget is reviewed.

The first twenty run `samples` stay unchanged for historical comparison.
`distinct_samples` retains the first twenty different measured tuples, with
their first retained run and timestamp. Both collections are bounded; the
distinct count saturates at twenty and is a lower bound thereafter, explicitly
marked by `distinct_shapes_saturated`. These are early-life observations, not
a recent distribution. Lifetime maxima continue increasing after saturation,
including dimensions from budget refusals. Such refusals do not advance the
preflight sample population.

Old measured samples seed the distinct collection, but historical maxima cannot
reconstruct missing samples. Migration also runs on unchanged-snapshot ticks,
so an old run-based `review_due` cannot remain green indefinitely. This changes
the follow-up review instrument only, not source selection or publication caps.

Snapshot budget samples include unattributed request populations: zero proven
WorkItems is a measured dimension, not zero workload. These snapshots pass the
same local size caps and have no canonical WorkItem query; their knot/read-back
requests still count toward the unchanged request and cooldown budgets.

After the bounded population completes, `publish_work_cost.py --review-only`
refreshes native health/review metrics without reading a method or contacting the
graph. It refuses an incomplete population or unresolved pending write, and
preserves the measured history. Display paths never invoke this mode.

## Refusals and review populations

Budget accounting is stored in `refusal_accounting` in the budget history and
publication receipt. `attempts` counts one changed invocation reaching a size
budget decision; `refusals` counts those refused before graph network access.
Backoff, unchanged snapshots, paused review refreshes, missing authority and
invalid source reads do not count as size decisions. An admitted decision can
still fail later on canonical-item lookup, transport or read-back; admission is
not successful publication.

`limits` counts each binding dimension on the first rejected snapshot (`items`,
`records`, `body_bytes`), or `snapshots` when the completed candidate collection
exceeds that cap. A refusal can bind multiple dimensions, so the sum of limit
counts is not a denominator. No claims are made about unexamined later snapshots.
The latest twenty refusal receipts retain source agent, harness, session and
snapshot identity plus measured dimensions. Each retained entry limits source
and dimension lists to two snapshots and reports source truncation. Lifetime
counters remain exact for observed decisions even when old detailed evidence
ages out. Transcript bodies are never retained in this accounting.

Prometheus exposes `camayoc_cost_budget_attempts_total`,
`camayoc_cost_budget_refusals_total`, and
`camayoc_cost_budget_limit_refusals_total{limit="..."}`. They are lifetime counters
for this state file, starting at
`camayoc_cost_budget_accounting_since_timestamp_seconds`. Historical refusals
before that timestamp are **unknown**, not zero. Preserve `refusal_accounting`
when archiving and clearing an accepted bounded shape population.

The existing `review_window_started_at` marker sets the next window boundary.
On its first observed tick, accounting records lifetime baselines and an
`observed_since` timestamp; window attempt/refusal gauges subtract those baselines.
The timestamp gauge `camayoc_cost_budget_window_observed_since_timestamp_seconds`
is essential when instrumentation began partway through a window. A window whose
start precedes observed coverage has incomplete refusal evidence; report the gap.
A deleted state file starts a new lifetime, visible through a new start timestamp
and counter reset. Refusal totals cannot reconstruct the missing history.

At the next review, read the receipt's budget history alongside the existing
admitted `distinct_samples`, source rotation evidence and metric timestamps.
The distribution is censored at the unchanged caps: its maximum describes the
largest admitted sample, not headroom. Exact shape equivalence remains
`(items, records, body_bytes)`. Repeated admitted shapes increase attempts without
necessarily increasing distinct shapes; that alone does not prove pinned input.

## Scheduler attempts before source selection

`--scheduler-only tick --publish-status` refreshes the attempt timestamp without
reading sources or querying the graph. It preserves the previous outcome,
request cooldown, pending write body, and budget history; a heartbeat is not a
successful projection. The scheduler calls it under its publication lock before
checking pending work, review pauses, cooldowns, or selecting an active source.

`--scheduler-only source_selection` (or `preflight` / `projection`) records an
early UNKNOWN outcome and its bounded reason. The existing failure and stale
alerts can then distinguish repeated failed attempts from missing reports.
`camayoc_cost_projection_unknown{reason="source_selection"}` identifies the
no-source path. Status-only calls neither read transcripts nor write graph data.
Deploy this publisher interface before a scheduler that invokes it.

## API-equivalent dollar projection

`work_cost.py` adds a separate `costs` object without changing token groups or
including repricing in the closure receipt. `data/model-prices.json` pins a
retrieval date, exact model IDs, first-party source URLs and USD rates per million
tokens. Results include the catalogue SHA-256 so a later rate revision is visible.
This is current API-equivalent consumption, not an invoice or a reconstruction
of historical subscription charges.

The projection prices each request before grouping: uncached input, cache reads,
cache writes split by measured lifetime, and output. Reasoning output is already
included in output and is not charged twice. Published whole-request long-context,
fast-mode and regional modifiers apply only when the required request metadata
is explicitly present. Missing category counts, cache-write lifetimes, model IDs
or modifiers produce an unknown reason. Unsupported service tiers and unpublished
aliases stay unknown. The dated Gemini promotional entry expires rather than
silently continuing at its old rate. No provider API calls are made by pricing.

Declared, inferred-held and unattributed totals remain separate. `usd` is null
when any contributing request is unpriced; `known_usd` is explicitly a partial
subtotal. Priced-token share and reasons expose coverage. Empty inputs are not a
zero-dollar observation. A missing or invalid catalogue leaves existing token
accounting available and reports unknown pricing. Pricing metadata becomes part
of the retained request evidence; changing a price table alone does not change
the token receipt.

`plan_share_usd` remains null until an independently measured account window and
plan allocation are supplied. No plan charge or allocation denominator is
invented from transcript token counts. Existing readers do not supply reported
Claude Code dollar totals; those cannot be represented as observed figures by
this projection. The catalogue uses the
[Anthropic](https://platform.claude.com/docs/en/about-claude/pricing),
[OpenAI](https://developers.openai.com/api/docs/pricing), and
[Gemini](https://aistudio.google.com/docs/pricing) price publications.

The metrics publisher recognizes inferred-token and API-equivalent metric names
as parser-session samples. Install that companion publisher before enabling new
rendered samples; recognition does not itself emit prices or establish a scrape.
