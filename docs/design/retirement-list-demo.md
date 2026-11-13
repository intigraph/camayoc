# Explicit retirement list demonstration

Status: source fixture; installation and live operator acceptance pending.

The same disposable SQLite fixture is exercised at base
`c72f62178ec2efc1849cf8ef56fd0fcd1f14a40b` and source candidate
`85fbe307276710248b31d850b8d76d92294a5a5c`. Watch the second explicit
retirement change from refusal to success, entries through eight succeed,
and the ninth remain refused. The candidate performs sixteen fixture absence
reads for the eight separate operator acts. An un-retired UNKNOWN still freezes
with zero writes. Releasing entry 2 preserves all eight audit records and
leaves entry 1 retired. A new, separately bound and absence-checked effect then
succeeds: eight active exclusions and nine preserved audit records.

[Recorded output](../demos/retirement-list/output.log),
[timing](../demos/retirement-list/timing.log), and
[probe](../demos/retirement-list/probe.py) are retained as small source artifacts.
Download the output/timing pair and replay with util-linux:

```sh
scriptreplay --log-out output.log --log-timing timing.log
```

Playback was checked. This is a terminal recording, not an inline video.
To repeat from separate base and candidate checkouts:

```sh
python3 probe.py "$BASE/scripts"
python3 probe.py "$CANDIDATE/scripts" --bounded
```

The library calls use a temporary journal and explicit fixture authority.
Absence responses are mocked, exact false and untruncated; there is no network,
live state, operator credential, receiver mutation or replay. The demo proves
source behavior, not live subject absence, receiver completion or deployment.
