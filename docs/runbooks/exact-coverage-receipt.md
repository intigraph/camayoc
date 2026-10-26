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

The query service may serialize reference subjects using its loaded `a:`
namespace binding. The adapter accepts full canonical subjects and the exact
`a:<requested-ID>` forms for the legacy namespace, then supplies full subjects
to the existing coverage helper. It does not expand arbitrary prefixes, match
suffixes, decode escaped lookalikes or promote identifier literals into IRIs.
Only `w`, constrained by the query's IRI `VALUES` and triple-subject position,
is decoded; foreign subjects and identifier-owner conflicts remain UNKNOWN.
An archived triple response is evidence of serialization, not a substitute for
the coverage query's exact result columns.

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

## Scheduled one-shot route (source only until separately activated)

`scripts/scheduled_exact_coverage.py` adds admission and durable attempt custody
around the read-only adapter. It does not install itself, arm cron, refresh source
or remove an entry automatically. Its default invocation emits OFF without
reading a plan, opening locks, resolving credentials or starting a child.

An activation packet must supply a private directory and owner-only JSON plan,
sealed by its SHA256. The exact two approved target IDs are sealed in that plan;
there are no command-line target overrides. The schema is:

- `format`: `camayoc-scheduled-coverage-v1`;
- `items`: exactly two distinct native IDs, positive control first;
- `control`: exactly the first ID;
- `board_graph`: the approved explicit board graph;
- `server`: the approved HTTP(S) origin, without path or embedded credentials;
- `tag`: a unique lowercase alphanumeric/hyphen entry tag;
- `source_sha256`: exact hashes keyed by all six script filenames below.

The child receives the sealed server origin explicitly, rather than inheriting a
cron default or caller override. Its actual server origin is checked in the
returned receipt; an alternate store cannot supply acceptance.

Pin `scheduled_exact_coverage.py`, `exact_work_item_coverage.py`,
`backfill_work_items.py`, `planes.py`, `quipu_auth.py` and `seed_work_items.py`
from one reviewed source revision. The old helper contributes coverage semantics
only; its legacy main is never invoked. A manifest does not grant activation
permission. Review its exact IDs, graph, hashes, paths and digest in the runtime
packet. No installed or scheduled acceptance is implied by these instructions.

The exact entry is constructed from the executing Python path, absolute wrapper
path, absolute private manifest path, manifest digest, `--enable`, hourly minute
17 and the unique `# camayoc-exact-coverage:<tag>` suffix. Use Python's `entry()`
function to compose it; do not hand-requote arguments. Only the normal neutral
cron editor may introduce it after separate approval and a fresh locked preimage.

Admission requires the current crontab to contain that exact entry once. A live
root-owned `cron` command-name ancestor and one matching root cron-service journal
CMD event in the preceding minute establish the independent start receipt. The
trusted journal `/usr/sbin/cron` executable identity, cursor/PID/start ticks and
exact-entry hash are retained before
the child. An environment variable or caller run-id cannot substitute. This
route intentionally supports Debian cron only: absent journal access, another
scheduler, a changed parent or incomplete attribution stops before the attempt.
It never falls back to a self-declared scheduled identity. Root-process executable
symlinks are not read: Linux gates those through ptrace permissions. Root UID
and command-name ancestry are paired with the trusted journal executable, not
replaced by a user-controlled command name alone.

The coordinator durably consumes the sealed invocation before admission, so a
busy or UNKNOWN admission cannot turn into a later cron retry. No child runs
until the independent scheduler evidence and source admission pass. It acquires
the existing producer source lock, then the existing Seeds writer lock, both
nonblocking without creating/replacing them. Both lock descriptors are inherited
by the pinned child so a parent crash does not release them during its reads.
A busy lock
stops with sanitized PID/executable-only holder evidence (UNKNOWN if unavailable),
never argv or a retry loop. Source pin, sealed plan and operator hold are checked
under those locks. Its exclusive `attempt.json` is flushed and its private
parent directory fsynced **before** admission or child execution. Independent
scheduler evidence and actual source hashes are separately flushed to
`admission.json` under both locks before the child.
An existing, partial or uncertain attempt is terminal. Crashes before the child,
during it, after it, or while retaining its result consume the attempt; there is
no reset/recovery replay command. Only the read-only adapter can be started.

The child retains its request/byte/deadline bounds. Private `stdout` and `stderr`
files retain raw evidence; `result.json` records the outcome. Malformed results,
timeout, inconsistent source/targets, or unknown budgets leave UNKNOWN with the
attempt consumed. There is no second invocation. Affirmative acceptance requires
both fixed IDs covered, all 15 bounded calls and zero writes. Raw response byte
counts are included per call; these measure response bodies, not network framing.
The scheduler journal evidence remains separate from the child's caller run-id.

After completion or failure, remove only this exact entry through the neutral
editor's locked filter operation, preserving current unrelated entries. The
filter invocation is the wrapper with `--cleanup-filter --manifest <path>
--manifest-sha256 <digest>`; it reads the current crontab on stdin and emits its
unchanged bytes except for the one exact entry. Missing/duplicated matches refuse
without emitting a partial replacement. Do not pass `--enable` during cleanup,
restore an old full crontab, delete attempt evidence or reset custody. Preserve
the editor snapshot/readback and independently verify default OFF, no remaining
entry, unchanged native records and the affirmative natural-run receipt before
reporting runtime acceptance. Source tests use private fixtures only.

### Private scheduler diagnostics

Scheduler admission failures retain bounded counts and enumerated first-rejecting
stages in the existing private `result.json`. `NO_MATCH` and `AMBIGUOUS` distinguish
zero and multiple accepted candidates; missing or unreadable evidence remains
`INPUT_UNREADABLE`, with unavailable counts represented by null, not zero.
Counts saturate at 65535 and are diagnostic only, never scheduler identity proof.
No command, arguments, journal message, cursor, PID or raw input is copied into
these diagnostics. The existing acceptance predicates, exact-one-match rule,
consumed attempt and no-child/no-retry behavior remain unchanged. A diagnostic
source update does not authorize installation or a new scheduled attempt.
