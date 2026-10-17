# Search context admission

**Status: implemented in the entity-link consumer; server and installed arrival
must be verified separately.** Camayoc has no search-based `retrieve` command;
`retrieve_metric.py` concerns cost accounting. `entity_link.py` is the search
consumer covered here.

Entity linking now requires Quipu's `quipu-context/v1` decision. Diagnostic
`results` never enter Jev's choices. Explicit insufficient context renders
`NO CONTEXT` and asks no model. An unsupported or missing contract, malformed
response, contradictory admission, or failed request is `UNKNOWN`, counted
apart from a successful abstention. It must not be read as zero entities.

A positive decision consumes only `context_results`, checking finite raw
`similarity` against the declared floor (at least the provisional 0.52). Ranking
or fusion scores cannot substitute. Subject/identifier and term coverage are
checked by the server; a high score alone never admits context. Source and valid
time remain attached to search evidence; admission is not verification of a
current cause. The existing entity filter, interleaving, self exclusion, pool
cap, inference quarantine and Jev none-of-these option remain in force.

Consumer validation requires a finite declared floor in `[0.52, 1]` before
accepting either CONTEXT or NO CONTEXT. A lower, missing or malformed floor
is UNKNOWN in these consumers, even when the server reports no context.

Deploy the server contract first, then the consumer. Older servers intentionally
produce UNKNOWN in the new consumer. Rollback uses an ordinary reviewed source
revert; preserve work-item links and evidence. No runtime activation, inference
campaign or corpus evaluation is implied by source tests.
