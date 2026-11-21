# Verified webhook recruitment foundation

A synthetic `check_suite.completed` payload, derived from the recorded check-run
fixture, is refused on base `b2845fcbb3eaac6a0c17ea5f4ccfde3c80b15fed` and maps to
its own check-suite entity on the candidate. The candidate source SHA-256 is in
the recording. A second demonstration uses an in-memory SQLite ledger and an
explicit fake spawn transport: two deliveries of one candidate call that
transport once, and the terminal receipt reads back.

Run `python3 docs/demos/github-recruitment/run.py` from the repository root.
[Output recording](output.log) and [timing](timing.log) are retained with this
source change. Download both and replay with
`scriptreplay --log-out output.log --log-timing timing.log`.
The output is also the readable transcript; no inline player is assumed.

These are source/library demonstrations, not a live recruiter, authenticated
spawn or installed acceptance. The separate drain, authoritative admission and
scoped principal transport are still required. No network calls or real jobs
are made by this recording.
