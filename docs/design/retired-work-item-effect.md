# Bounded explicit ingress retirement

Status: source implementation; live retirement and scheduled acceptance pending.
This is producer scheduling exclusion, not a receiver completion fence.

An evicted or process-lost `/knot/status` receipt is UNKNOWN. Exact effect
absence does not prove that the old worker stopped. Neither elapsed time nor a
newer native version permits replay of that retained envelope.

## Explicit operator boundary

`retire_work_item_effect.py` defaults OFF. It requires the existing dedicated
SQLite state and the producer's protected native authority on stdin. It takes
the same state lock as the scheduled producer. Retirement performs two bounded exact-subject absence reads while holding that
lock and the journal transaction: all named graphs, then the default graph.
Both must return explicit, untruncated false within 4096 bytes. Present subjects
must complete instead; unknown, missing or partial evidence refuses. Inspect
and release perform no HTTP requests.
The operator must supply an independently reviewed item, write ID, request hash,
version and effect transaction, plus an audit reason. Every binding is compared
with the exact stored envelope in one transaction before exclusion is recorded.
The request hash uses the same sorted-key JSON encoding as the writer.

At most eight active retirement entries exist per journal. Released audit
history is retained without consuming active capacity. Each invocation retires exactly one bound effect; there is no list
argument or batch release. The known one-slot and lifetime-cap schemas upgrade atomically,
preserving every original field and released record. Unknown schemas refuse.
There is no automatic age threshold, bulk operation, cursor reset, changed
capacity or automatic retirement of subsequent UNKNOWN effects.

```sh
python3 scripts/retire_work_item_effect.py retire --enable \
  --native-authority-stdin --state "$CUSTODY_FILE" --item "$EXACT_ITEM" \
  --write-id "$WRITE_ID" --request-sha256 "$REQUEST_SHA256" \
  --version "$SOURCE_VERSION" --effect-tx "$EFFECT_TX" --reason "$RULING_REF" \
  < "$AUTHORITY_FILE"
```

The command retains the complete original body, request bindings, transaction,
reason and timestamp in `retired_effect`. The original pending row remains;
newer feed candidates can advance its candidate transaction but cannot replace
its body or remove the exclusion. Other items use the unchanged delivery path.
An un-retired UNKNOWN still stops its tick and retains custody. A second
retirement requires its own operator act and fresh absence reads; a ninth
refuses while eight are active. After an explicit release, a new independently
bound absent effect can retire; its monotonically increasing slot preserves history.

## Visibility

`inspect` with the same protected authority and state prints bounded bindings
and the active count. Every response includes all active bindings (at most eight),
plus a history page of 32 records by default. `--history-limit` accepts 1 through
64; `history_has_more` and `history_next_after` report coverage. Continue with
`--after-slot` at that cursor. History pages omit full bodies and never delete
older records. The full envelope stays queryable locally in
`retired_effect.body` under the existing journal permissions; it is never
printed to logs. Receipts disclose `retired_effects`, and scheduled publication
adds `camayoc_workitem_ingress_retired_effects`. Alert on a positive value;
UNKNOWN metric publication is not a zero. A monitoring-rule rollout is a
separate operational gate, not implied by this source change.

`backlog` counts schedulable pending rows. Retired effects are separately counted,
so backlog zero and exit zero do not certify that all effects were resolved.
The old worker may still have committed or may commit later. Its item remains
excluded across restart and receipt eviction.

## Reversal

The per-item explicit `release` verb requires the same item/write ID/request hash and an
operator reason. It removes only the scheduling exclusion and preserves the
retirement record. It changes no other entry and frees one active slot without reusing an audit ID.
It does not clear the pending body, acknowledge a version,
prove absence/completion or send anything. The next normal tick must reconcile
that original frozen effect under the unchanged status/readback rules; UNKNOWN
still freezes it. The old absence-only release command cannot release an active
retired effect.

Durable receiver-side completion/admission evidence is separate work. It must
survive restart and eviction, bind exact owner/ID/body, and fence late workers;
this producer retirement does not implement or claim that protocol.

A source rollback does not downgrade the journal or undo exclusions. Once the
list schema is in use, do not enable the old one-slot retirement/release helper
against it. Keep manual operations off until a list-aware helper is available;
use its per-item release when an exclusion must be reversed.

The [recorded fixture comparison](retirement-list-demo.md) shows the explicit
list boundary and preserved UNKNOWN freeze without contacting a live receiver.
