# Native control receipt attribution

Trigger: the first item verifies, then the second item's WorkItem ASK is false.
Before, `item` names the first verified item and does not identify the failed
attempt. After, `item` stays compatible, `attempted_item` names the second item,
and `control` classifies the false response without retaining response payloads.
Both versions remain UNKNOWN with requests=8, writes=2, verified=1, backlog=4,
and the second item's exact pending envelope retained.

Base: 727e025666a3a979c8f20ebe5c0b555bc7089465.
Candidate implementation: e23a71957b00f219548e7d5f43ecde701070c649.
The recording also prints the candidate adapter's SHA256, binding the actual
bytes. Target: a synthetic library fixture with a private temporary journal,
no live server. This demonstrates diagnostics only, not incident recovery.
The control predicate, acknowledgements, limits and cadence are unchanged.

The small sanitized terminal recording is retained here with its timing and
repeatable probe. Download `output.log` and `timing.log` to replay:

~~~sh
scriptreplay --log-timing timing.log --log-out output.log
~~~

Repeat from the repository root (with Python test dependencies installed):

~~~sh
python3 docs/demos/native-control-attribution/probe.py
~~~

The base revision must be available in local Git history for the comparison.
Playback requires a terminal with util-linux scriptreplay; no inline video
playback is claimed. No production assay, replay or adoption is authorized.
