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

Run from the repository root with a `quipu-server` binary available:

```sh
python3 docs/demos/memory-write-safe/demo.py
```

Replay the captured run with util-linux:

```sh
scriptreplay --log-out output.txt --log-timing timing.txt
```

These are small synthetic stores, not a production snapshot. No ontology is
loaded or proposal accepted. The earlier read-only `/validate` demonstration
does not exercise this post-state boundary. Snapshot-scale materialization,
rollback and production recall remain separate acceptance gates.
