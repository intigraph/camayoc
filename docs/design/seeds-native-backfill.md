# Seeds-native backfill source

This source-only adapter is default OFF. It does not register a schedule,
initialise standing ingress, consume a change cursor, or authorise a live scan.
The legacy br adapter remains available for explicitly selected historical
stores, but is not the native source and must not be rearmed for a Seeds board.

## Source and custody

`seed_work_item_backfill.py` consumes the existing protected
`camayoc-tracker-authority-v1` projection on stdin. The selected server must match
Camayoc's transport; graph and prefix cannot come from candidates or legacy data.
Each discovery reads at most 17 identifiers from that graph, in increasing
identifier order. Sixteen are retained, the seventeenth is a lookahead. There is
no unbounded list, coverage scan, per-item br dependency process, or fallback.

Output caps do not prove server-side read cost. Production admission must measure
the exact discovery query and exact-subject reader on the selected board, under
the existing read-cost gate. This patch supplies no such allowance. A complete
historical sweep/bulk ingestion requires its existing reviewed authority.

Each candidate is then read through the existing canonical Seeds Reader, including
native blockers. Native Actions remain status, owner and dependency authority;
the existing source-owned metadata snapshot writer projects searchable metadata.
It uses the same WorkItem and snapshot identities as normal ingress. It never
uses a new episode per item or replaces another source's links.

Discovery checkpoint and candidate retention commit in one SQLite transaction.
The dedicated backfill journal rejects ingress state, another peer/board, or
another metadata plane/actor. It retains exact pending writer bodies before send.
Lost responses reconcile through existing controlled readback, never resending.
Four serial deliveries maximum and 17 total requests maximum share the existing
slow-read stop rule. Failed discovery is UNKNOWN, not successful empty coverage.

The queue's zero transaction token is local bookkeeping, not a source transaction
or an initial change-feed cursor. The discovery checkpoint is a current-state
keyset sweep, not a consistent as-of snapshot. New identifiers behind the keyset
need a separately admitted new sweep. `enumeration_exhausted` means only that the
last discovery reached its end; it does not certify current whole-board coverage.
Pending items must also drain with verified receipts. No automatic reset exists.

## Admission and rollback

Offline callers may invoke the default-OFF command with a private `--state`
path and `--actor`; it performs no I/O. A separately approved native run adds
`--enable --native-authority-stdin`. A new dedicated `.sqlite3` state needs explicit
`--initialise`, only once; reopening with it refuses. Parent directory, projected
query/dataset, selected credential, writer coordination and schedule belong to
the existing deployment path. No bearer is accepted on argv or printed.

Do not share writable custody with a running ingress tick. Both writers use the
same source snapshot identity; a runtime launch needs the existing writer hold
and admission gates. A source merge is not that approval.

Rollback removes only the admitted backfill schedule/activation and reverts the
source merge. Retain all pending bodies and journals, accepted graph facts,
original disabled br schedule, and dataset/query receipts. Never migrate/reset
custody or replay to make coverage appear complete.
