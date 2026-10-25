# Native ingress without a feed poll

The same one-item source fixture is applied before and after. Watch `feed_polls`
and `requests`: polling costs one feed read plus four effect/verification calls;
the stream-custody path costs zero feed reads and four effect/verification calls.
Both verify one effect and retain cursor 10. SQLite and the sync functions are
real; HTTP and input delivery are fixtures. This is a library recording, not
installed stream traffic. The separate loopback test covers real SSE delivery.

Base: Camayoc `078798a`.
Candidate product code: `e15804cbb514b10fb40ce81b23d7a80521877067`.
Later recording files do not change that implementation.

```sh
git show 078798a:scripts/seed_work_item_sync.py > before_sync.py
python docs/probes/native-stream/reproduce.py before_sync.py
```

Use the repository test dependencies. Retain the small recording and readable
`transcript.txt` here with the PR. This is a terminal output/timing pair, not an
inline video. Download/decompress and replay:

```sh
gzip -dc docs/probes/native-stream/output.txt.gz > output.txt
scriptreplay --log-out output.txt --log-timing docs/probes/native-stream/timing.txt
```

The temporary `before_sync.py` is not part of the source. No service, credential,
schedule, enrollment or production writer is used by this demonstration.
