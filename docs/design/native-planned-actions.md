# Native planned-work reads

Implementation: source candidate; stored-query activation requires independent
review. Existing open-plan and lapse definitions remain unchanged.

After a tracker moves to Seeds, its `schema:Action` already exists at creation.
An old br-backed projection must not supply a newer-looking answer from an older
snapshot. The native board owns status, owner, modification time, labels, title
and description. Deferred work remains deferred. A native read failure is
UNKNOWN; it never falls back to legacy status.

Use bounded semantic retrieval to propose identifiers, then ask
`camayoc_planned_actions` for each exact identifier. Alternatively, a real
creation event supplies its own identifier. The reader accepts at most25
distinct IDs, requires a native board visibility control, and refuses conflicting
fields, truncated responses or more than100 raw rows. It does not enumerate the
board, increase an ingress batch, restart a producer, or replay old state.

`scripts/planned_actions.py` takes an explicit board, records plane, question and
one or more `--id` arguments. It ranks the returned candidates by one to three
distinct lexical terms in the native title and description. For example, the
question `review role for verification` can retain a role-design candidate with
the explicit matched terms `review` and `role`. This is retrieval evidence, not
an assertion that verification and review are synonymous, nor a promise that
all relevant items were found. Narrow a question or improve the upstream
semantic retrieval when it misses; never report a bounded zero as board-wide
absence.

Only `stiwi-directive`, `design` and `plan` labels admit this reader's planned
kind, with that precedence. Open, in-progress, blocked and deferred actions are
unimplemented candidates; closed actions are excluded. Labels classify a view;
the reader does not add a graph type or alter an item's labels.

One further bounded query reuses existing about links from legacy records with
the exact same identifier. It never selects their status, owner or timestamps.
Missing links remain missing; no model-generated edge is written. No query or
reader writes to the board, changes activity clocks, or activates an expiry.

The earlier broad native text query hit the served10-second timeout. This
implementation instead binds the exact identifier before reading fields. Its
fixture tests must execute with rdflib, and its live acceptance must measure the
same bound queries against the selected board before activation. Source tests
alone do not prove retrieval completeness or natural creation-to-reader arrival.
