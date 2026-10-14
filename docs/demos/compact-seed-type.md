# Compact seed type reader

A native seed blocker whose type is returned as `schema:Action` and whose
status is deferred must produce BLOCKED. The reader previously produced UNKNOWN.
This source fixture transcript uses the unchanged board fixture from
`tests/test_seed_blocked_by.py`; it makes no production calls.

```text
Before (compact schema:Action, deferred blocker): UNKNOWN
After (same fixture): BLOCKED
Control (full IRI): BLOCKED
```

Base: `9ab17eb`. The candidate adds Schema.org prefix expansion in `_iri`.
The before result replaces `_iri` with its base behavior; the after and
full-IRI control use the candidate unchanged.

Reproduce the parity checks across open, deferred, closed, tombstone and missing
statuses with:

```sh
python3 -m unittest discover -s tests -p test_seed_blocked_by.py -v
```

This is a readable transcript; asciinema is unavailable on the recording host.
It proves source behavior, not installation or scheduled runtime acceptance.
