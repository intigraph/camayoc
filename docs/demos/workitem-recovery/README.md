# Bounded WorkItem recovery

The same timeout/HTTP503 incident consumes three writes at simulated offsets
0, 480 and 2280 seconds; the store becomes healthy at 2400 seconds.
Before: still UNKNOWN, zero verified at four hours.
After: the retained body verifies at 5880 seconds (98 minutes), without a manual
retry. Every body is identical and every tick stays within three requests and
one write. A healthy populated positive control verifies in both versions.

Base source: 3074376f13a7277978a9b93435dd4aeb83d1a62e.
Candidate source: 0ca956f7450ff066a16cfbd149d3da77afe14a62.
Target: selected source checkouts with a private state file and fully controlled
store callbacks. Time is accelerated; this is not a production outage or write.
The primary Seeds custody implementation is outside this demonstration.

The small sanitized terminal recording is retained here with the source.
The output and timing were captured together, replay checked, and inspected for
private paths/hosts. Download output.log.gz and timing.log to replay:

~~~sh
gzip -dc output.log.gz > output.log
scriptreplay --log-timing timing.log --log-out output.log
~~~

To repeat either revision, check it out separately, then run this probe with the
selected checkout path:

~~~sh
python3 probe.py /path/to/selected/source
~~~

The displayed source hash binds the actual adapter bytes. Additional tests cover
hourly spacing, restart, expiry/unknown clocks, retained refusals, invalidated
absence evidence and backlog fairness. No live replay or backfill is authorized.
