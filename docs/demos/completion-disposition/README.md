# Pending completion disposition

A retained pending effect whose existing completion lookup returns `unknown`
used to leave no completion detail. The candidate retains fixed diagnostic bits.
A `stopped` worker with unproved absence still cannot release the effect.
Both revisions make exactly one completion lookup for the same fixture input.

Base: `49ba314f1b208ea159a03ed4d641cc18c477721d`.
Demonstrated implementation: `efac443b` (later documentation/recording only).
Target: source-level mock; no installed service or live request.

Run `python3 docs/demos/completion-disposition/demo.py` from the repository root.
The reproducer extracts base and current source, supplies the same status
response, and checks unchanged decisions and lookup counts. The independent
absence prover is mocked false; this demonstrates diagnostics, not actual
absence or recovery.

[Recorded output](output.log) and [timing](timing.log) are a replayable terminal
recording retained here as small review fixtures. Download both, then run
`scriptreplay --log-out output.log --log-timing timing.log`.
The output is also a readable transcript; no inline video player is implied.
