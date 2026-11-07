# Domain inference source and raw alias gates

Target: isolated RDF fixtures and staged source, based on Camayoc `7363f71`.
The original three property/rule inputs are unchanged. Their new domain/shared
placement must recombine to the exact same RDF graph. The fixture then shows
that raw alias equivalence detects a missing class mapping and a missing calendar
link instead of treating rewritten queries as evidence.

Run from the repository root with its review dependencies:

```sh
python3 docs/demos/domain-inference/reproduce.py
```

The [terminal recording](output.log) and [timing](timing.log) retain actual
output and timing from that invocation. The recording was replayed locally:

```sh
scriptreplay --log-out output.log --log-timing timing.log
```

Download both files together; browser inline playback is not asserted. The
output log is also the readable transcript. This
source/data demonstration has no installed before/after: publication, production
shape-set identity, revalidation, raw snapshot coverage and rollback rehearsal
remain separate acceptance gates. No production data or endpoint is accessed.
