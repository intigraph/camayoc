# Native comment routing fixture

Status: source qualified; installed emitter and outbox acceptance pending.

Base `c72f62178ec2efc1849cf8ef56fd0fcd1f14a40b` rejects a native comment
change as an invalid item ID. Candidate `227c9824ba2ce89865f933177999733b69732eb7` maps that comment
and its parent change to one exact parent policy key. The fixture forbids all
HTTP calls and uses synthetic identities; it touches no database or live state.

[Output](../demos/review-comment/output.log),
[timing](../demos/review-comment/timing.log), and
[probe](../demos/review-comment/probe.py) are retained. Replay was checked:

```sh
scriptreplay --log-out output.log --log-timing timing.log
```

Repeat with separate base and candidate checkouts:

```sh
python3 probe.py "$BASE/scripts"
python3 probe.py "$CANDIDATE/scripts" --fixed
```

Malformed comment paths and noncanonical parent spellings still refuse;
foreign graph and prefix changes remain outside this policy. This recording
proves routing behavior, not successful scheduled delivery, receiver scope,
outbox recovery, or deployment. It is a terminal recording, not inline video.
