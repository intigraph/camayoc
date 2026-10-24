# Scoped event burn-down demonstration

This is a replayable terminal recording of a private **library fixture**, with no
production calls. Base `93295fc` has no event producer. The candidate enrolls one
open item without inventing historical creations, processes close/reopen in two
transactions, and reports one open item and one closure. Coverage stays
`catching_up` until an empty complete feed page, including when unrelated global
transactions advanced the watermark.

Run from the repository root:

```sh
bash docs/demos/workitem-burndown/run.sh
```

Replay the retained output and timing with util-linux:

```sh
gzip -dk docs/demos/workitem-burndown/output.log.gz
scriptreplay --log-out docs/demos/workitem-burndown/output.log \
  --log-timing docs/demos/workitem-burndown/timing.log
```

[Compressed readable output](output.log.gz) and [timing](timing.log) are retained with this source
revision in the existing `docs/demos/` directory. Playback requires downloading
both files, decompressing the output, and `scriptreplay`; no browser embed is claimed. The PR caption binds
the recording to its exact proposed head.

This fixture demonstrates transition accounting and coverage, using synthetic
native change frames. The separate private native integration verifies actual
schema refusal/no-write, pinned enrollment, ASOF change replay, effect readback
and the stored query. The native cost fixture checks all daily balances and
closure counts independently. None proves live board enrollment, installed
Prometheus metrics, a scheduled run, a stale-alert drill or historical backfill.

The same-feedback bulk-transaction regression is repeatable with:

```sh
python3 -m unittest discover -s tests -p test_work_item_burndown.py
```

A single 70-item close contributes 141 observations. Preparation checks the
verification plan before committing pending custody; normal delivery verifies
all observations and advances once. A lost reply followed by reopened custody
uses only readback. Missing or altered observations keep the original pending
body and cursor, without a resend. These private transport controls supplement
the retained recording; the original transition-accounting demonstration still
applies. The separate native 70-item check also confirms open=0 and closed=70
following a committed write whose reply is lost, then read-only recovery.
