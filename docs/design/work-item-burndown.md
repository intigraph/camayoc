# WorkItem event burn-down

**Status: opening slice implemented in source; production enrollment and metric
acceptance remain separate.**

The producer consumes bounded, whole-transaction native change pages using
`capture=new_row`. Each row is the canonical Action **as of its transaction**;
using the latest metadata projection instead would lose intermediate close and
reopen transitions. It does not read the board on every scrape.

An operator enrolls one explicit assignee/priority cohort from a bounded query
pinned to a committed source transaction. This records an opening balance, never
invented creations, closures or status history. Subsequent transactions contribute
immutable observed deltas. Assignment and priority moves enter or leave the
cohort; a deletion is not a closure. Duplicate rows within a transaction count
once. A conflicting frame, missing authority, partial response or version
regression refuses without moving the cursor.

The opening slice uses the existing governed Observation class and public
schema.org/Seeds predicates. No new event class or pending DORA shape proposal
is required. `queries/camayoc_work_item_burndown.json` follows the same
load/name/description/template/typed-params layout as the DORA stored queries.
Both use operational window graph registration and labeling. Their source
streams, custody and writers remain separate.

The stream's enrollment window is its stable custody address. A month boundary
never silently changes that graph or discards opening balances. The stored query
reads only this explicitly selected event graph, groups contributions by UTC day
and returns a bounded relation. The exporter computes daily prefix sums. It emits
only days at or after enrollment, with an explicit coverage-start timestamp;
this is not a global-board or reconstructed 30-day history claim.

A nonempty feed page leaves coverage `catching_up`. An empty, complete page proves
the selected graph is caught up even when unrelated global transactions advanced
the watermark. Missing, stale or catching-up checkpoints are UNKNOWN, not zero.
The scraper performs one stored `/ask` evaluation, requires exact query identity,
complete rows and a result in less than four seconds. That evaluation is store
work even though its HTTP route is not `/query`.

Ingress separately budgets at most one `/changes` GET, one `/knot` and one exact
`/query` verification per tick. A pending effect uses only the verification read.
It is written durably before transmission and is never blindly resent after an
uncertain outcome. Peer, board, assignee, priority and stream identity bind the
SQLite custody; existing foreign custody is never adopted or reset. Enrollment
is explicit, pinned and positive; there is no automatic initial cursor or cohort.

`work_item_burndown_cli.py` exposes explicit enrollment, ingress and scrape
commands. It receives the protected native authority projection on stdin.
The deployment wrapper is default OFF and adds no schedule. Normal source
review, query registration, window labeling, scoped enrollment and deployment
receipts are required before runtime acceptance. Existing retired snapshot
collectors are not rearmed.

Verification includes real close/reopen and membership changes, deletion versus
closure, duplicated/conflicting frames, restart with uncertain delivery, tampered
readback, missing coverage and transport request budgets. The private native
benchmark registers the exact stored definition over 48,800 unrelated subjects
and 6,306 observations, then compares every daily balance and closure count to an
independent oracle in three fixed trials. No production endpoint or existing
store can be selected by the benchmark.
