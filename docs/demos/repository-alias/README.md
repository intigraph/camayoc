# Repository identity demonstration

A valid legacy-owner receipt changes from literal refusal to acceptance only with the declared owner map and matching numeric repository identity. Missing-map and wrong-ID controls still refuse.

The target is an offline synthetic fixture, not an installed service or a live
release. GitHub replies and publication are mocked. Before uses base `f6372f6e43e762385b0b573c2ca5432afff544c9`; after uses the checked-out candidate
source. Published assets, tags and invocation records are untouched.

Run from the repository root:

```sh
python3 docs/demos/repository-alias/reproduce.py
scriptreplay --log-out docs/demos/repository-alias/output.txt \
  --log-timing docs/demos/repository-alias/timing.txt
```

[Recorded transcript](output.txt) and [timing](timing.txt) are retained with this
source change. This is a replayable terminal recording, not an inline video.
The recording proves the bounded fixture behavior; it is not release acceptance.
