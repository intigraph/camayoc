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
available. Hook mode processes at most 20 records within a 750 ms network budget,
never writes output into the prompt, and exits 0 on every outcome. The hook also
continues if the CLI is missing or fails. A second replayer never waits on the
first. Ordinary `cy flush` returns 1 while records remain; hooks must use the
hook mode. No daemon, cron entry or additional prompt hook is installed.

The bounded buffer accepts at most 16 MiB, with a 1 MiB limit per new row.
Corrupt, partial, symlinked or over-cap queues are UNKNOWN and retained. Disk or
permission failures cannot promise capture: hook exit 0 means the harness may
continue, not that persistence succeeded. Queue and replay locks use Unix flock.
Enabling automatic capture, installing memory shapes, and replacing file memory
remain separate deployment steps.

The capture producer (gshu.4) uses its own FIFO with the same lock and
acknowledgment primitives via the companion capture adapter. `cy flush` replays
both streams when that adapter is installed. Without it, capture backlog remains
pending and is reported; the buffer never calls the older dropping replay path.
Ordering is per producer stream, not a global order between capture and typed
records. The source patch for the adapter depends on the capture branch.
