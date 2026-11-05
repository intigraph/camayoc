# Explicit bootstrap namespace

This terminal recording runs the same bootstrap regression in disposable stores
against base `05f92e2d780bf4f0bf0291851e98a93ca462eda0` and the candidate.
The base refuses `Observation` with HTTP 400. The candidate admits it, reads back
its exact class, rejects an unknown type without writing facts, and rejects an
invalid provenance tag. The native server is clean Quipu 0.17.0 (`647c2dd2`).
This demonstrates source behavior, with no production changes.

Download [output.log](output.log) and [timing.log](timing.log), then replay:

```sh
scriptreplay --log-out output.log --log-timing timing.log
```

The recording requires a local terminal player; inline playback is not provided.
Reproduce the candidate through the project-managed Python environment:

```sh
just review-test -q tests/test_bootstrap_rerun.py tests/test_bootstrap_credentials.py
```

These small recording files are retained with the source for review.
