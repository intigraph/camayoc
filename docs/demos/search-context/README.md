# Entity-link context demo

The same controlled response contains a diagnostic neighbour and an explicit
NO CONTEXT decision. The actual base parser returns one candidate; candidate
63d64f7980c7ab479bc7e8c08da6baab888ea389 returns zero. A separate admitted
positive remains one, and a legacy response changes from candidates to UNKNOWN.

Target: source library functions with local fixtures, no model, network or live
store. Base: cd06b1b3e3ff19b8d064b0a785361dce61500023. The recording uses
the committed implementation; later demo-only commits preserve that behavior.

[Terminal recording / transcript](recording.txt), [timing](timing.txt).
Run `python3 docs/demos/search-context/run.py` from the repository to repeat.
Replay from this directory with:

```sh
scriptreplay --log-out recording.txt --log-timing timing.txt
```

This is a terminal recording, not an inline video. It proves source candidate
selection, not publication, installed delivery or quality on a large corpus.
Small text recordings remain with this demo in Git.
