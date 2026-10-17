# Exact plane-fact supersession

This terminal recording compares baseline
`6e1556a14476273e254bbf928bcb3f0ec2e936da` with the candidate source identified
by the two SHA256 values in the recording. Each run uses a fresh private
loopback Quipu store with the same ten target facts and one inferred replacement.
It performs no production writes.

Ordinary promotion leaves two current claims. `--supersedes old.nt` leaves
exactly one: the old fact is closed but visible as-of, all nine sibling facts
remain, and the inferred source stays open. The command responses and read-back
measurements are actual output, not a constructed success transcript.

Run the same fixture from the repository root:

```sh
python3 tests/demo_supersede_plane.py
```

It requires `quipu-server` on PATH (or `QUIPU_SERVER_BIN`) and the pinned baseline
Git objects. The fixture provisions only disposable loopback stores and uses a
synthetic credential. It does not establish installed-client or production
acceptance. Store valid-time has one-second precision; the fixture crosses a
clock boundary before closing and checks the historical cut before and after.

[Readable recording](output.log) and [timing](timing.log) are retained here as a
small source-review artifact. Download both and replay with util-linux:

```sh
scriptreplay --log-out output.log --log-timing timing.log
```

Playback requires a terminal; no inline video embed is claimed. The producer
command completed successfully, and local replay was checked. The initial demo
import-path failure and the earlier fractional-time fixture failure are retained
in private task evidence, not represented as successful runs.
