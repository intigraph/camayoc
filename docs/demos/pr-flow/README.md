# Mergeability observation demonstration

Run `python3 docs/demos/pr-flow/demo.py` from the repository root. The same
synthetic input on base bf4e470 leaves mergeability and base SHA UNKNOWN; the
candidate retains the explicit false/dirty observation and base SHA. This is
an actual source mapping transcript, not a GitHub/Quipu service recording.
The small text artifacts are retained with source.

`--observe-mergeability` is opt-in. Its per-PR detail calls share the poller's
existing request budget and backoff. Unchanged open-list ETags still permit
budgeted detail checks; unchanged semantic snapshots are deduplicated. API
null mergeability stays unknown. Partial, malformed or duplicate lists cannot
replace the previous complete open roster or prove a closure.

The additive shape changes require governed review and deployment before this
flag is enabled against a live writer. This source change does not install a
shape, issue a token, schedule a runner or arm a job. Budget exhaustion means
coverage UNKNOWN, including for large repositories.

Detail deduplication remembers only the last consecutive semantic state in an
owned spool cursor. The cursor and queued bytes commit together. A returning
state after an intervening change receives a new delivery sequence and an
observation-time-bound activity identity. Unchanged observations, including
after restart or a failed writer retry, retain the original queued bytes.
