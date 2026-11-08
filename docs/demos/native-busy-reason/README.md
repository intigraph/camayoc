# Native BUSY reason replay

Run `python3 docs/demos/native-busy-reason/demo.py`. It invokes the real native caller against private fixtures, with a real held file lock and a fixed clock just before the 60-second boundary. The baseline emits identical BUSY output; the candidate adds `busy_reason` to distinguish `lock-held` from `interval-not-due`. Both preserve custody and the stored receipt, perform no effect tick and publish no new freshness. This demonstrates output semantics, not a production cause or rate change.

[Output](output.log) and [timing](timing.log) are an output-only terminal recording. Replay downloaded files with `scriptreplay --log-out output.log --log-timing timing.log`. Playback was verified; no inline playback is claimed. Both historical revisions are pinned in the script. The later minute-slot rule has a [separate replay](../native-minute-admission/README.md).
