# Review deadline demonstration

Before: base `f712c17c` has no persisted review-deadline adapter. After: one
authenticated fixture event arms a real SQLite deadline, refuses an early fire,
records one synthetic launch at two hours, and refuses replay. A review cancels
the second target. Authority reads show the explicit one-plus-two request budget.

The clock, trusted event verification, authority and launcher are synthetic.
SQLite, cancellation and the shared candidate ledger are real. No HTTP, installed
scheduler, credentials or actual job launch is used.

From the repository root:

```sh
python3 docs/demos/review-deadline/run.py
scriptreplay --log-out docs/demos/review-deadline/output.log --log-timing docs/demos/review-deadline/timing.log
```

The output/timing pair is a terminal recording; download and replay it with
`scriptreplay`. The PR caption supplies the exact candidate source identity.
