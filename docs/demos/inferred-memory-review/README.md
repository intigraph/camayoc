# Inferred memory review

An overdue, typed and keyed inferred Preference was absent from both full and
incremental review. The candidate returns `DUE` through both paths; the same
age on a non-memory inferred fact remains excluded.

The recording compares base `088c403d7541eca1acad9a112aa8ab35d5410134` with the
candidate revision containing this directory. It uses an isolated RDFLib graph,
not an installed service. Reproduce from the repository root:

```sh
bash docs/demos/inferred-memory-review/run.sh
```

[Terminal recording](output.log) and [timing](timing.log) are retained here with
the source. Download both to replay with `scriptreplay --log-out output.log
--log-timing timing.log`. The output log is also the readable transcript;
inline video playback is not provided. No production graph writes occur.
