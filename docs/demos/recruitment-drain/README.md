# Recruitment inbox drain: offline source demonstration

The same verified redelivery is applied to a pre-populated terminal inbox after
ordinary graph delivery history is pruned. Base
`bd752d0bce33844a67cb6d7f0f15cef164828651` raises `IntegrityError`; source candidate
`4f357b1acb1664938206d08d22e20d3bcab010fc` returns `duplicate` and preserves the
tombstone. Both use the same isolated terminal fixture; no real job ran.

The separate consumer then processes two verified wakes with fixture authority
and a fake controller: one confirmed fixture receipt and one duplicate, exactly
one fake-controller call. Graph deliveries remain pending, showing that episode
writing is independent. Retention clears both terminal payload bodies while
preserving delivery tombstones and the candidate attempt ledger.

[Recorded output](output.log) and [timing](timing.log) are retained alongside
this caption as small source-review artifacts. Download both and replay with:

```sh
scriptreplay --log-out output.log --log-timing timing.log
```

Run `python3 docs/demos/recruitment-drain/run.py` from the repository root to
repeat. The transcript records exact ingress/drain source hashes. The follow-up
recording commit does not change those modules.

This is offline source qualification with fake authority/transport callbacks.
It does not prove installed consumer scheduling, real custody resolution,
controller key enforcement, adapter deadlines, live spawning or end-to-end
latency. No CLI, service or live transport is added; production inbox and
recruitment activation remain off. Tombstones/attempt metadata remain durable;
this is payload retention, not database compaction or forensic erasure.
