# Canonical memory validation demo

This historical recording describes the previous candidate at the exact hashes
below. Current generated profiles use per-predicate cardinality instead; see the
[post-state write demonstration](../memory-write-safe/README.md). Passing this
read-only validation did not establish write safety.

This is a replayable terminal recording of read-only native `/validate` calls
against fixture data on Quipu 0.16.6 (`dafe8bfedddfa01adf39cbc96e0d2f7e2a82e50f`).
It demonstrates candidate profiles, not installation or actual memory capture.

The input is one canonical Directive with a natural key but no `rule`. The
legacy-only profile passes it because that profile targets only legacy IRIs.
The generated canonical profile with explicit compatibility paths refuses it.
Adding the required rule to the same input passes as a positive control.
The broader validation run also checks valid Preference records, legacy
Directives without natural keys, and a conflicting rule split over both IRIs.

The before profile is the scoped legacy source
`shapes/agent-memory-scoped.shapes.ttl`, corresponding to the legacy proposal
constraints at base `0a4c3f8b22fb988322519322f791a5df69077ae9`.
The after profiles are `02-shapes-new-classes.json` and
`03-shape-directive-scoped.json` in `docs/design/proposals/agent-memory/`.
Their exact Turtle SHA-256 digests in this recording are respectively
`b54a3359daa07aaa449d7447bc7c91b8041c3cc296c51a44c3a8f3f0f1fed870` and
`ab1c2e698174630106e94ca46020d686cebb5c2af608f88cd5b909e3bc42c0b5`.

Replay after downloading both files, with util-linux `scriptreplay`:

```sh
scriptreplay --log-out output.txt --log-timing timing.txt
```

[Readable transcript](output.txt) and [timing log](timing.txt) are retained here
as small source evidence. Inline playback is not claimed. No request headers,
credentials or production memory content are recorded. For a fresh probe, send
the equivalent fixture and each profile to an authorized isolated `/validate`;
this recording grants no production load or record-write authority.
