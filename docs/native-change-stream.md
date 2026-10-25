# Native metadata change stream

The finite native sync tick still supports the explicit legacy polling path.
For a persistent deployment, `sync_work_items.py --native-authority-stdin
--native-stream --stream-identity identity.json` holds one scoped
`/changes/stream` subscription. Supply the existing `--state`, `--actor` and
`--source` arguments and the same protected authority projection on stdin.
It refuses a missing journal, `--initial-tx`, unknown store identity, wrong
capture, foreign graph, malformed or oversized transaction. There is no tail
reset, automatic enrollment or polling fallback.

The protected identity file contains exactly `store_id` and `log_epoch`, each
an independently verified nonempty string. Before switching, the separately
reviewed `--bind-stream-source --expected-stream-cursor N` transition binds those
values to the existing journal under its producer lock. It requires the exact
current cursor and never changes pending effects or the cursor. An existing
binding cannot be replaced. Normal stream startup refuses missing or changed
bindings before opening a connection; a modified identity file cannot silently
adopt prior custody into another epoch. This source does not execute that
transition in production. Transport connection alone never
proves current data: the source remains UNKNOWN until a complete transaction
confirms that identity. A changed epoch stops admission; it is not adopted.

The standard-library decoder is copied byte-for-byte from Chaski, with its
immutable origin and hash in `protocol/change-frames-origin.json`. No separate
multipart parser or RDF projection is implemented here. The stream supplies
`old_and_new_values`; custody explicitly accepts that richer capture alongside
its prior `new_values` pages, preserving old edge references. `new_row` remains
refused. Complete transactions must fit the existing 512 KiB/4,096-record
custody bounds; staging can lower its budget but cannot raise it. An oversized
transaction stays UNKNOWN, with the prior cursor retained.

The network reader queues at most one complete transaction. Only the SQLite
owner thread commits candidates and the cursor together, then acknowledges
transport delivery. Projection effects remain separate: pending bodies are
prepared before a write and verified before acknowledgement. A lost response
is read back, never blindly resent. The existing maximum four serial effects,
request ceiling and minute cadence remain unchanged; idle ticks have no feed
request. No new graph query is issued per heartbeat.

Deploy through the existing service-management lane. First stop the finite
producer at a natural boundary; preserve its journal, receipt and lock; verify
the source identity; then select this persistent mode. It holds the same lock
for its lifetime, so an overlapping finite tick cannot poll or write. Record
the actual installed source, cursor progression, natural delivery and measured
per-client route rates. This source PR does not start a service or change a
schedule. Roll back by stopping this mode and restoring the prior finite
command with the same custody files; never rewind, reinitialize or discard a
pending effect.
