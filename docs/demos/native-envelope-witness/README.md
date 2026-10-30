# Envelope witness demonstration

Synthetic library fixture: target `demo-a` verifies, then `demo-b` has a false
WorkItem control. Before, the receipt identifies the failed target but has no
envelope witness. After, its graph, exact request-body hash, new-write branch and
proved write response are attributable to `demo-b`. Both runs keep UNKNOWN,
one verified target, eight requests, two writes and the same control result.
No installed producer or live store is invoked.

Base: `4b1607cf08f60bd40c1a33a47b83410b2096f6d3`. Candidate sync source blob:
`e60d171c59c9fd48ef6f8819a7f6a30a50b6e390`. Publication-only changes do not alter this demonstration.
Reproduce from the repository root:

```sh
python3 docs/demos/native-envelope-witness/probe.py --compare
```

[Readable recording](output.log) and [timing](timing.log) are retained as small
source fixtures in this established demo directory. Download both and replay
with util-linux `scriptreplay --log-out output.log --log-timing timing.log`.
Local replay was verified; this is terminal replay, not an inline video.
The recording demonstrates source behavior only, not installed acceptance,
ingress root cause, archival retention or release of the runtime hold.
