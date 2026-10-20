# Native backfill demonstration

Target: synthetic source fixture; no network, installed service or credential.
Base: `135d92a95ba349e3c0662767fa624c09d30119f4`. Candidate content is
this commit's adapter and tests; the PR identifies its exact head.

Before, the native backfill entrypoint is absent. After, the command defaults OFF
with zero requests/writes. The fixture discovers 19 native records in two bounded
pages, projects each identity once through the existing writer double, and makes
zero requests after the sweep completes. A lost writer response becomes UNKNOWN;
reconciliation performs two reads and zero new writes. The recorded request counts
are observable library effects, not production performance or graph coverage.

[Output transcript](native-backfill-output.log) and
[timing log](native-backfill-timing.log) form a util-linux terminal recording.
Download both, then replay with:

```sh
scriptreplay --log-out native-backfill-output.log --log-timing native-backfill-timing.log
```

The recording was replayed locally. Inline playback is not provided. To reproduce
from the repository root with Python dependencies installed:

```sh
bash docs/demos/native-backfill-demo.sh
```

The RDF paging test executes the query against an isolated RDF dataset; the demo's
writer is a test double. Live read-cost admission, installed runtime smoke,
scheduler registration and natural coverage proof are separate gates.
