# Exact two-item coverage receipt

`scripts/exact_work_item_coverage.py` is a read-only, default-OFF adapter for a
separately authorized coverage proof. It installs no schedule and never invokes
the legacy tracker, writes a snapshot, resets custody or updates the global
coverage cache. Source approval does not authorize a scheduled activation.

Supply exactly two distinct item IDs, one positive control among them, one
explicit board graph and a bounded caller run identity:

```sh
python3 scripts/exact_work_item_coverage.py --enable \
  --item demo-canary --item demo-witness --control demo-canary \
  --board-graph https://example.invalid/board --run-id example-proof
```

The run identity is **caller-declared**, not an attestation that a scheduler ran
it. A scheduled acceptance needs the separately approved schedule, source hashes,
locks, actual scheduler receipt, preimage and rollback. Do not manually invoke a
production proof to manufacture that acceptance. Without `--enable`, the command
returns OFF with zero requests and does not resolve credentials.

The adapter preserves `backfill_work_items.covered()` type/identifier union
semantics across ROOT, observed records and the one explicit board graph. Every
read is restricted by `VALUES ?w` to the two canonical metadata subjects and two
Seeds subjects. It executes exactly fifteen bounded queries, with no pagination
or inferred graph. It refuses foreign subjects, conflicting identifiers, excess
rows, truncation and missing controls. A restricted subset never replaces the
full-board cache. This proves coverage only for the fixed subject forms, not
global identifier uniqueness or absence at arbitrary aliases. `NOT_COVERED` is
not permission to mint or re-post an item.

Each response is limited to 256 KiB and each whole request to six seconds; the
invocation has a seventy-second read budget. The standalone Unix transport uses
a request timer as well as a socket timeout, so a trickling response cannot renew
the deadline. It refuses redirects and an existing caller alarm. Unsupported
timer use fails rather than silently removing that bound. It uses the shared
Quipu credential resolver and provenance headers; error text does not expose the
credential. There are no retries.

| Outcome | Exit | Meaning |
| --- | --- | --- |
| `COVERED` | 0 | Both items covered and the positive control proved |
| `NOT_COVERED` | 1 | Control proved; the other item is absent from coverage |
| `UNKNOWN` | 2 | Coverage not established; never an empty success |

The JSON receipt includes the exact IDs/graphs, covered and missing IDs only for
known outcomes, per-call timing/counts, zero writes, source hashes, timestamp and
caller run identity. Keep it in its own evidence location. Retain UNKNOWN
receipts; do not reset or automatically retry a scheduled proof.

The tests execute both this adapter and the existing helper over private RDF
fixtures to prove both outcomes, and separately cover missing controls,
malformed/refused/truncated answers, owner conflicts, timeout interruption,
default OFF, redirects and CLI exit codes. They contact no production server.
