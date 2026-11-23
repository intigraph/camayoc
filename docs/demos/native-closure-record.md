# Native closure projection demonstration

The private fixture stores a nanosecond close timestamp and an exact reason.
The base metadata projection loses both. The new explicit `closure_record()`
projection retains them in the same single exact-subject read. A missing
close timestamp remains UNKNOWN rather than becoming the modification time.

Base: `e86c5da25585cb90a68989bb68cdefcf758841e2`. Candidate implementation:
the `seed_work_items.py` in the commit containing this recording. This is a
source-library demonstration with an injected query response, not an
installed producer or live feed. Metadata callers keep their existing API.

The [recorded output](native-closure-record.txt) and
[timing file](native-closure-record.timing) are a small terminal recording,
retained with this source demonstration in Git. Playback requires downloading
both files and util-linux `scriptreplay`; inline video playback is not provided.

```sh
scriptreplay --log-out docs/demos/native-closure-record.txt \
  --log-timing docs/demos/native-closure-record.timing
```

To repeat the actual comparison from the repository root:

```sh
python3 docs/demos/native-closure-record.py
```

The reproducer needs the named base revision in local Git history. Its only
temporary file contains that revision's module; it does not contact a server
or start an event subscription. The output and timing were inspected and
replayed before publication.
