# Bounded public CONSTRUCT

The same private producer snapshot and native executable run the original and
optimized queries with a 30,000 ms query budget. The recording shows the original
query timing out after 30.413 seconds, then the candidate returning 96,530 triples
in 24.757 seconds. The two private-node exclusion sets are projected to distinct
subjects before `MINUS`; the publication predicates and refusal rules are unchanged.

[Recorded output](output.log) and [timing](timing.log) are small, retained
util-linux terminal artifacts. Download both and replay them with:

```sh
scriptreplay --log-out output.log --log-timing timing.log
```

Replay and the raw transcript were checked. This is a terminal recording, with
raw CRLF bytes, rather than an inline video. To repeat from the repository root,
supply the qualified native executable and a retained indexed producer database:

```sh
PL07_ENGINE=/path/to/qualified/quipu \
PL07_PRODUCER_DB=/path/to/retained/producer.db \
python3 docs/demos/bounded-construct/run.py
```

The script creates a coherent SQLite backup in a disposable directory. It never
queries a service or publishes anything. The large input database and provider
bytes are not included in Git. Different input sizes or machine load can change
timing; the script checks the actual timeout and successful graph result.

Baseline source is `b3b80b1ac93dac0069cce4a47496598bbec4d6da`; qualified runtime
source is `e21e76cf3aa4d95efd59a117c7052f4c0b4a7f63`. Later commits in this change
only add these demonstration artifacts. [Qualification data](qualification.json)
binds the query and test digests, native executable and provider pins, and all
native import/promotion start/end times and exit codes. Argument hashes preserve
command identities without publishing private input paths.

On the same retained input, a complete RDF-term comparison found all 96,530
triples identical, with zero missing or extra triples. The fresh build from the
new source separately admitted 96,542 triples and passed its README positive
control and the unchanged native receiver verifier in 733.112 seconds, within
the existing 900-second whole-run bound. These are different input snapshots;
the fresh graph is not claimed byte-identical to the retained one.

| Fresh phase | Seconds | Exit |
| --- | ---: | ---: |
| Index | 258.754 | 0 |
| CONSTRUCT and export | 51.438 | 0 |
| First import | 191.393 | 0 |
| First promotion | 5.980 | 0 |
| Verifier outward share | 0.898 | 0 |
| Second import | 206.029 | 0 |
| Second promotion | 5.242 | 0 |
| Complete receiver verifier | 220.119 | 0 |

The verifier duration includes the second import/promotion, not additional work
to add to those rows. It retains missing-policy, blocked-bytes, incomplete-rule
and exact-catalogue controls. Expected negative controls exit nonzero; the JSON
records those outcomes. Every native run used the same Quipu 0.16.6,
model/tokenizer digests, embedding dimension and provider settings. HOME guards
reported 158 paths and five UID resources unchanged. This proves private native
qualification, not a release, installation or recovery of an earlier publication.
