# Governance source demonstration

Run `uv run --script docs/demos/governance/measure.py` from the source checkout.
The same class/shape coverage probe compares the named base with the candidate
source, including the nonzero Directive control. The Camayoc arm also measures
loader presence. This does not execute a deployment or certify a policy adapter.

The output-only terminal recording is `output.log` with `timing.log`.
Replay after downloading both files:
`scriptreplay --log-out output.log --log-timing timing.log`.
Playback requires util-linux scriptreplay; no inline video is promised.
The recording is retained with this source proposal in this demo directory.
