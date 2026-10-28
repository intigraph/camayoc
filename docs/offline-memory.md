# Offline memory buffer

`cy record` and the corresponding MCP tools buffer locally when Quipu is
unreachable. When a queue already exists, another valid record joins its tail
rather than overtaking it. Local field and secret checks apply before buffering;
a refusal is never made valid by going offline. A queued result is pending, not
confirmation that the record exists in Quipu.

The FIFO is `$XDG_STATE_HOME/camayoc/memory-queue.jsonl`, falling back to
`~/.local/state/camayoc/memory-queue.jsonl`. It is private (0600), with short
exclusive locks for appending and acknowledging records. Network calls hold only
a separate replay lock, so producers can append during replay. Records are
fsynced before returning `queued`. Legacy JSONL rows remain readable.

`cy flush` replays at most 100 records within a ten-second network budget. It
stops at the first refusal, failed/unconfirmed write, malformed row, changed
endpoint/plane, or local error. That head and its tail remain queued. Each
successful acknowledgment rereads the queue under lock, preserving arrivals
during the request. A crash after a remote write but before acknowledgment may
replay the same call; the existing create tool's unchanged/supersession handling
applies. This is ordered, at-least-once replay, not an exactly-once transaction
with the store. Other online writers remain outside that replay transaction.

New rows retain source agent/session metadata and the target endpoint/plane, but
never credentials. Replay uses current credentials while preserving that source
metadata. A changed endpoint or plane stops replay; restore the original
configuration deliberately rather than sending the queue to another store.
Legacy rows have no trustworthy origin, so replay does not attribute them to the
current agent/session. Malformed or refused heads need operator inspection;
`cy flush` never deletes them automatically. Keep a backup when repairing a row.

`cy backlog` is a read-only JSON count (`camayoc-backlog/1`) summing the typed
FIFO and the capture producer's `capture-spool.jsonl`. Missing means zero;
unreadable or malformed means `queued: null`. It creates no directory. Bobbin's
companion memory-header reader counts the same local JSONL, reporting a positive
backlog or UNKNOWN without printing queued content, invoking a CLI or contacting
Quipu. Its existing prompt eligibility and memory-lane switch still apply.
A zero backlog adds no line. Deploy both changes to enable this header surface.

The existing SessionStart hook invokes `cy flush --hook` when the CLI is
available. Hook mode starts a detached worker processing at most 20 records within a
750 ms network budget, never scans the spool on the harness thread, never writes
output into the prompt, and exits 0 on every outcome. The hook also
continues if the CLI is missing or fails. A second replayer never waits on the
first. Ordinary `cy flush` returns 1 while records remain; hooks must use the
hook mode. No daemon, cron entry or additional prompt hook is installed.

The bounded buffer accepts at most 16 MiB, with a 1 MiB limit per new row.
Corrupt, partial, symlinked or over-cap queues are UNKNOWN and retained. Disk or
permission failures cannot promise capture: hook exit 0 means the harness may
continue, not that persistence succeeded. Queue and replay locks use Unix flock.
Enabling automatic capture, installing memory shapes, and replacing file memory
remain separate deployment steps.

The capture producer uses a separate FIFO. `cy flush` replays both streams;
ordering is per producer, not a global order between capture and typed records.
Capture append reads only file sizes and one tail byte. Replay rotates the
active spool to `capture-spool.inflight.jsonl` under the append lock, then scans
and acknowledges that isolated older prefix without blocking hook appends.
An existing in-flight file remains the older prefix and counts in backlog.
Stop older-version flushers before upgrading the rotation protocol.

Capture records older than seven days, refused writes, malformed/torn fragments
and unknown/changed destinations move to durable local quarantine before replay
continues. Network uncertainty still stops ordered capture replay. Each visited
record/fragment counts against the budget. Failed archival or acknowledgment
retains the head, and a retry reuses the same archive ID. Nothing is silently
retargeted or recorded as delivered because it left pending replay.

`cy backlog` adds `quarantined_captures` (null when unreadable), separate from
`queued`. `cy capture quarantine --json` lists IDs, reasons, timestamps and
retention limits without raw text or any network operation. After restoring the
original endpoint/plane, `cy capture quarantine --requeue` moves eligible entries
to the pending tail without contacting Quipu. It preserves original origin,
timestamp and target; expired, malformed and still-mismatched entries stay out
of replay. Run `cy capture flush` afterward to attempt delivery. Durable append
precedes archive removal, and a pending marker prevents duplicate appends after
a failed local unlink. If a prior copy already replayed, retry retains the
normal deterministic-IRI, at-least-once semantics.

Private quarantine archives have a 16 MiB encoded-byte cap, a 1,024-entry cap
and a seven-day age limit since archival. Background/shared replay maintenance
and explicit requeue prune expired files; insertion also evicts oldest archives
to meet capacity. Shared maintenance runs even if the typed FIFO is blocked.
Age cleanup is lazy until the next maintenance run, never part of hook append
or read-only inspection. Reported retention counts distinguish expiry from
capacity eviction and orphan cleanup. A private 0600 metadata-only ledger in
`capture-quarantine-retention.json` preserves cumulative counts, discarded bytes
and the last eviction time across silent background runs. Both
`cy capture quarantine` (text/JSON) and `cy backlog` expose `retention_totals`.
Inspection leaves the counters and their mtime unchanged; only explicit
`cy capture quarantine --clear` acknowledges them and discards archives.
Unreported eviction is refused if its durable receipt cannot be written;
interrupted counter commits are recovered without counting a removal twice. Back up evidence before it expires. The separate capture
pending-age limit is not reset by requeue.

`cy capture quarantine --clear` deliberately discards all remaining archives;
it cannot be combined with `--requeue`. Capture flush exits 1 while quarantine
needs attention. See [the capture design](design/memory-capture.md) for the
failure and retention details. The typed FIFO's refusal policy is unchanged.
