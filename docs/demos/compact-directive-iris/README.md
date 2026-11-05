# Compact directive capture IRIs

A synthetic graph returns the same capture episode using `a:`, `aegis:` and a
full IRI. The baseline loses the `a:` timestamp and reports LAPSED immediately;
the candidate preserves the two-day grace. The expired control still lapses.
No network or installed service is exercised.

Run `python3 docs/demos/compact-directive-iris/probe.py` with baseline
`f44db6616a58d2291fca096c63f424b3f09e6804` available in Git. The recording prints
candidate source hashes; subsequent recording-only commits preserve those bytes.

[Actual terminal output](output.log) and [timing](timing.log) can be downloaded
and replayed with:

```sh
scriptreplay --log-out docs/demos/compact-directive-iris/output.log \
  --log-timing docs/demos/compact-directive-iris/timing.log
```

This small recording is retained with the source as review evidence.
