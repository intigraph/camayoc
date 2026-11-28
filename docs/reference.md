# Reference

Every entry point is a script under `scripts/`, most with a `just` recipe.
Each takes `--help`.

| command | what it does |
|---|---|
| `scripts/bootstrap.sh [--with-claude-hooks]` | reach or install a quipu, load ontology + shapes, prove the gate |
| `scripts/competency.py "<question>"` | is a question covered by the competency suite? (`--map` asks Jev and returns a stored query) |
| `scripts/competency.py --list` | the whole suite, with its watermark |
| `scripts/query_coverage.py` | which competency questions have a stored query that answers them |
| `scripts/planes.py status` | the quarantine planes, and whether each is labelled |
| `scripts/settled_decisions.py "<proposal>" --declared <file>` | does a proposal collide with a decision already made? |
| `scripts/jev.py noul\|choice\|score ...` | ask Jev for one typed decision (needs a TypeSafe key) |
| `scripts/jev_mcp.py` | the same, as an MCP server (declared in the plugin) |
| `scripts/backfill_work_items.py` | every tracker record as a WorkItem, paced and resumable |
| `just check` / `just test` | the local quality gate and the test suite |

The competency questions themselves live in
[`competency/`](https://github.com/intigraph/camayoc/tree/main/competency); the
stored queries that answer them live in
[`queries/`](https://github.com/intigraph/camayoc/tree/main/queries).

Measured on 2026-11-28: **124 competency questions** across eleven slices —
**44 stored**, **33 expressible and unwritten**, and **47 competency gaps**
(`just query-coverage`). The query library contains **67 stored queries**;
that counts query files, not covered questions. Re-run the coverage command
and count the 66 `queries/*.json` plus one `queries/*.rq` file to refresh
these figures.
