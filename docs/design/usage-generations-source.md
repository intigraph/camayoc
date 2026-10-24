# Staged usage generation source

This implements the source-only SRM option A decision. It does not enroll a
producer, accept/load a proposal, create a production graph, or declare runtime
acceptance. Proposal 42 and its approved proposal 43 attribution delta remain
separate from normal source review and deployment. The exact revised profile
is `shapes/usage-generations.shapes.ttl`; the original contract and the narrow
amendment are retained in the adjacent design documents.

## Entry points and scope

`publish_work_cost.py --post --generation-policy POLICY.json` explicitly selects
A for that policy's session when C refuses its item count, or when A already
owns its generation. Omitting the option retains C. C and A share the same
producer lock, pending-write fence and hourly/minute write ledger when enrolled.
A may not publish without the existing observed-record read grant, an exact
loaded profile match, an explicit frozen-legacy declaration and a runtime
acceptance reference. No policy is installed by this source change.

The policy schema has exactly `enabled`, `producer`, `harness`, `session`,
`legacy_frozen`, and `runtime_acceptance`. An enabled policy requires both
booleans true, an existing reviewed producer IRI, an exact native session ID,
a known harness, and the separately reviewed runtime acceptance reference.
These fields are trusted deployment configuration, not a substitute for the
runtime evidence or permission to manufacture it. The normal policy author
must bind that evidence; a nonempty string alone is not operational authority.

One generation contains disjoint stable-request parts plus its manifest.
A part is at most 40 requests and 64 KiB of final encoded HTTP body. A metadata
fragment contains zero usage records, is bounded by the same 64 KiB write
budget, and has its own tick. It records the complete expected nonempty-part
set; it is not counted as a request partition. This permits empty correction
generations without inventing a new class or claiming an empty token source.
The parser must still establish the supplied input has no errors.

Graph creation and kind-labeling each occupy their own tick. Kind labels are
`knowledge`; this source assigns no trust rank, authority or signing identity.
Part staging uses at most three requests: direct canonical WorkItem preflight,
write, exact lossless readback. Graph-local reference stubs contain only a
preflight-proven WorkItem type and the identifier as label, never a guessed
canonical title. They live only in the generation graph and are excluded from
cost rows. No canonical tracker graph or original identity fact is rewritten.

The manifest is written and verified only after every request part has a durable
receipt. Activation is a marker write and one graph-bound complete-set query,
at most two requests. The reader validates graph identity, all required term
kinds/cardinalities, canonical JSON, horizon, source/manifest/part digests,
counts, request identity, correction lineage and attribution state. Unexpected
facts, missing rows, mixed generations, legacy graphs and truncation refuse as
UNKNOWN. Source-log completeness and global per-seed coverage remain separate.

## Load and failure bounds

The shared lane admits at most one C-or-A write per minute and 60 writes/hour.
A body bytes are at most 3.75 MiB/hour; combined C+A bytes cannot exceed the
measured prior worst-case hourly envelope. Aggregate planned/staged unselected
body receipts are capped at 64 MiB. Unknown state is never reset to zero.
A single over-budget record or metadata envelope refuses before graph writes.
An unchanged selected source is not restaged.

The planner retains one selected generation and one staging/inactive slot plus
the frozen legacy source. After a correction selects the next generation, the
old A graph stays in the inactive slot. Another generation refuses until the
separately governed retention step frees it. No retirement, deletion or hidden
storage growth is implemented here. This conservative stop is an explicit
operational dependency, not permission to delete retained graphs.

Pending is persisted before every write; an uncertain response or mismatched
readback keeps it. There is no same-tick retry or automatic pending clear. A
verified receipt can recover only a lost local cursor update by exact body
hash, without resending. Existing cooldown rules and the shared rate fence
remain in force; slow calls retain the longer backoff.

## Read consumers

The opt-in `session_usage/work_cost_generation` retrieval method accepts exactly
producer, harness, session, horizon and consumer (`st-cost` or `brief`). It uses
one cached attempt fence per consumer across sessions, with at most one query
per five minutes. No new cadence or background poller is introduced. The query
returns selector and generation facts in one response, with explicit STR,
isIRI, DATATYPE and LANG columns and the normal truncation indicator. It does
not confuse an IRI-looking literal with an IRI or silently accept a capped
standard-format result.

A failed or rate-limited build returns UNKNOWN with any prior complete snapshot
explicitly separated as `last_complete`. It never substitutes it as fresh cost.
The pre-existing metrics, Brief cohort admission and C coverage limitations are
not bypassed. This source adds no production method declaration or variable flip.

## Rollback and remaining acceptance

Pause generation publication and retain the generation-aware reader, pending
bodies, manifests, receipts and graphs. A pointer rollback may expose only
explicitly stale/UNKNOWN older data. Never resume C concurrently for the same
session, clear pending, or delete stage/legacy graphs as a rollback shortcut.

Runtime still requires the governed profile disposition/load/readback, a
real-shaped replay at production-copy size with the signed latency limits,
the fleet 408 gate or explicit lead ruling, then the matched after-watch.
The small private native controls are source correctness evidence only.
