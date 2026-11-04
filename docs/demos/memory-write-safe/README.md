# Post-state memory profile demonstration

The recording runs the actual regression tests against disposable loopback
Quipu servers with SHACL write validation enabled. The old alternative-path
cardinality control permits an unrelated write before the shape is installed
and refuses it afterward. Replacement profiles permit valid legacy, canonical,
and mixed-field records while refusing missing, duplicate-per-predicate and
wrong-type fields. A later shared-record-field conflict is refused without changing
the retained field. Cross-alias rule values pass: this is a disclosed limit. Unrelated writes continue after semantic refusals.

The candidate bounds each literal predicate separately. It does not enforce the
former maximum over the union of aliases; cross-alias values can double that
bound, including on later writes. Production admission must resolve this limit
or explicitly restrict producers to one spelling. No writer flip is included.

Run from the repository root with the post-state comparison baseline:

```sh
QUIPU_MEMORY_SERVER_BIN=/path/to/quipu-server python3 docs/demos/memory-write-safe/demo.py
```

Replay the captured run with util-linux:

```sh
scriptreplay --log-out output.txt --log-timing timing.txt
```

These are small synthetic stores, not a production snapshot. No ontology is
loaded or proposal accepted. The earlier read-only `/validate` demonstration
does not exercise this post-state boundary. Snapshot-scale materialization,
rollback and production recall remain separate acceptance gates.

CI fetches the released `quipu-ai-v0.16.6` server for these two tests, archive
SHA256 `18c31265c540b4db9434484e58ea91ac64afb68bb1dcceb244e1e79623377d24`,
separately from the legacy v0.14.1 compatibility server and report CLI. The
archive's server identifies source `ad8de357521acdbd9f7a0175a37b7c20d3a21f5c`.
The legacy server predates the post-state behavior and cannot establish these
controls. A promised but missing memory server fails rather than falling back;
the integration verdict still requires zero skips. The old-profile refusal is
a characterization of this pinned baseline, not a required defect in future
engines. The retained recording shows the original source-profile comparison;
the released baseline also passes the same unchanged assertions.
