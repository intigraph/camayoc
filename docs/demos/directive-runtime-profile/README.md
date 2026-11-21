# Directive deadline release profile

The compatibility release previously carried the older Directive traceability
shape even after the deadline contract changed upstream. This refresh carries
that exact reviewed shape from revision
`1fc72ba6a57132d063bb38af9ded2521595fcbcb` into the software-factory profile.

Run the composed-artifact controls:

```sh
uv run --frozen --python 3.11 --group review python -m pytest \
  tests/test_aegis_compatibility_bundle.py \
  tests/test_aegis_domain_packs.py \
  tests/test_aegis_directive_deadlines.py -q
```

Observed result: **14 passed, 112 subtests passed**. Three Directives with one
zoned, typed deadline each pass with either supported Quechua predicate. Missing,
untyped, wrong-namespace, unzoned and multiple deadlines fail with a Violation.

The aggregate remains 120 node shapes and is RDF-isomorphic to the exact pinned
upstream input. The software-factory artifact changes from
`1e652d5f607f311636b69a737bdce5ab84010997db548d98052a53bb22aea641`
to `fd82a0e0533a772ada0f9945b4b986c5b1a79781030b662a2fe7606b0d391cc3`.
The other five artifact files retain their hashes. The compatibility compiler
and its equivalence checks are unchanged.

This is source and artifact qualification. Runtime activation requires a
separate consumer fixed-pin update and ordinary deployment; these tests do not
claim a loaded-set result.

The [recorded output](output.log) and [timing](timing.log) replay with:

```sh
scriptreplay --log-out output.log --log-timing timing.log
```

Playback was checked with `scriptreplay`. The [reproducer](run.py) compares the
base revision printed in the recording with the candidate artifact hash above.
The same three dated Directives change from admitted with Warning to admitted
without Warning; the undated control changes from admitted with Warning to
refused with Violation. Both targets are isolated source fixtures. The small
terminal recording is retained here with the source; no inline player is assumed.
