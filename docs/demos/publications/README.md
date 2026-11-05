# Publication ingress demonstration

Synthetic local source fixtures; no HTTP write or installed producer.
The identical artifact.published input is quarantined by base a4151d70d9b5310166e7c897aeac054f9c60be80
and becomes a ReleaseEvent at candidate 61500884c8daa61049bed68fa6bfdd0c84f6fa60.
The record retains publication time, repository, resolved commit and actor.
Removing the commit then refuses. The base command must exit 2; the complete
demo exits 0. HOME/resource guard remained clean.

Download [output.log.gz](output.log.gz) and [timing.log](timing.log), then run:

    gzip -dk output.log.gz
    scriptreplay --log-out output.log --log-timing timing.log

This is a terminal recording requiring download and util-linux scriptreplay,
not an inline video. The [actual transcript](transcript.txt) is the text
fallback. The candidate code is unchanged by this documentation commit.

To repeat the mapping control, pass
[the committed fixture](../../../tests/fixtures/cdevents/artifact-published.json)
to each revision with:

    python3 scripts/ingest_cdevents.py artifact-published.json --actor demo

The recording proves local source normalization and refusal, not live schema
admission, workflow activation, historical event coverage or Brief timing.
