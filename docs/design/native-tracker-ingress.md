# Native tracker ingress

The Seeds path uses one explicit deployment-selected graph, server and prefix.
The scheduled wrapper projects that selection; neither a candidate feed nor a
legacy knowledge node selects tracker authority. `review_due` receives the same
selection as a protected root-owned `/etc/camayoc/tracker-authority.json` file.
The file contains exactly `format`, `backend`, `server`, `graph`, and `prefix`;
format is `camayoc-tracker-authority-v1`, backend is `seeds`, and the server must
match the configured transport. There is no missing-file or failed-read fallback.

The canonical reader binds the exact native Action, validates its own identifier,
version, status, owner and timestamps, and limits each response to 128 rows and
256 KiB. Current title, description, labels, kind and explicit links share that
read. Native status/owner/activity govern idle disposition. Independent declared
review dates and verification ages remain independent; a deferral does not
satisfy them. A failed native read remains UNKNOWN even if an older observation
would resolve the obligation.

`sync_work_items.py --native-authority-stdin` consumes the selection as bounded
JSON on stdin. It requires a separate `.sqlite3` state, and retains the existing
one-record/60-second budget. It performs at most five calls and one projection
write per tick. It does not modify cost attribution or budget policy.

A new custody database requires an explicitly reviewed `--initial-tx`. The
schedule supplies no initializer. Deciding that cursor, the treatment of the
pre-cutover coverage gap, and any bounded initial inventory or backfill are
separate operational decisions. Existing custody cannot be reset or silently
converted from a legacy JSON state. Do not use a global feed watermark to skip
history in an empty selected-graph page.

The GET `/changes` feed supplies candidate IDs only. Pages contain up to four
whole transactions, 4096 rows, and 512 KiB. Cursor and up to 256 coalesced pending
IDs commit together before effects. A partial or oversized transaction refuses
as a whole. Pending effects retain their exact body before the writer call;
a lost response freezes that effect until controlled readback, without resend.
Later candidates do not overwrite its body. Failed candidates rotate so other
retained work can progress. A full pending budget drains retained work while
reporting UNKNOWN coverage. Source versions cannot regress. A repeated version
is read back without another writer call.

Current metadata reuses the exact WorkItem identity and atomically replaces only
this producer's snapshot claims in the observed knowledge plane. It makes current
title/body/labels searchable on the WorkItem itself, and updates or removes
source-owned links while preserving other sources' claims. Legacy exact-ID links
remain enrichment; they do not override native status. Comments are not indexed
by this projection. No Observation, verification, idle deadline, DreamLane
periodic policy or expiration policy is fabricated by the metadata writer.

Source merge alone does not enroll the producer. Adoption requires reviewed
source and configuration, compatible installed versions, explicit custody
initialization/coverage disposition, one named scheduled producer, and observation
of a natural scheduled run and eligible item arrival. Search consumers must pass
actual hit IDs to native reads. The runtime owner separately approves the arrival
SLO and load budget. No forced ingest, reindex, deadline reset, alert clear, or
automatic rollback to the retired source is part of this path.
