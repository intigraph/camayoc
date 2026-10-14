# Close scratch-store readers before publication

The same native governance resource is staged while SQLite connection objects
remain referenced, modelling delayed garbage collection. Base `24b1b60` fails
with `OperationalError: database is locked`; the candidate stages all 449 direct
declarations. This is a scratch-store library probe, not an installed adapter
or live shape load. Quipu 0.13.0 is the native producer and reader.

The [terminal recording](output.log.gz) preserves actual output and timing.
Download it, run `gzip -dc output.log.gz > output.log`, then replay with
`scriptreplay -t timing.log -s output.log`. No embedded-video claim.

To reproduce from this checkout with an explicit native Quipu CLI:

```sh
git show 24b1b60:scripts/governance_loader.py > /tmp/loader-before.py
export DEMO_BASE_SOURCE=/tmp/loader-before.py
export QUIPU_BIN=/path/to/quipu
uv run --no-project --python 3.14 --with rdflib==7.6.0 python docs/demos/governance/sqlite-close/probe.py
```

Both paths create new temporary databases. The probe retains handles without
closing them on the loader's behalf until after each attempt. The candidate
must close its own connections before consolidating and publishing the store.
The small recording stays with this demo; it contains no credentials or host
configuration.
