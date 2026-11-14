# Directive evaluation deadlines

This [terminal recording](output.log), with [timing](timing.log), compares the
same controlled read responses against exact base and candidate source. It is a
source consumer demonstration with mocked Quipu reads, not installed alert proof.
The recording prints the exact base revision and candidate code SHA256 values.

A future explicit deadline keeps the directive fresh after the old two-day grace
would have paged; a past explicit deadline pages before that old grace expires.
The separate stored-query test executes the actual query on a private RDF dataset.

Replay locally with `scriptreplay --log-out output.log --log-timing timing.log`.
Small sanitized logs are retained with this example; no inline playback is claimed.
Repeat from the repository root:

```sh
uv run --frozen --python 3.11 --group review python docs/demos/directive-deadlines/demo.py
```
