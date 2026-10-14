# Planned-work discovery queries

Status: IMPLEMENTED IN SOURCE — deployment and live acceptance are separate.

The tracker projection keeps immutable observations. An old open snapshot is
not evidence that an item is still open, and an old lapse firing is not evidence
that it is still idle. These two queries select the latest observed tracker
state before showing either finding.

## Questions answered

- Which plans, designs and directives are still open, who owns them, and what
  existing entities are they about?
- Which open directives have a recorded review-due firing and no tracker
  activity since that firing?

`camayoc_open_plans` returns Plan, Design and Directive items whose latest
snapshot says `open` or `in_progress`. It returns status, owner, observation time,
recorded `about` links and the tracker snapshot containing the title. The `topic`
parameter is a case-insensitive substring of that snapshot. It does not infer
synonyms: use graph search to identify a concept and inspect its recorded links.
An empty topic lists the planned-work population.
Both queries require an explicit `records` plane parameter. The open-plan query
also requires `topic`, including an explicit empty string for the full listing.

`camayoc_lapsed_directives` composes the tracker records plane with the root
plane's `reaction-entity-review-due` firing events. It excludes a firing after
later tracker activity, closure or deferral. It reads recorded lapse evidence;
it neither computes an unrecorded expiry nor invokes a reaction. The historical
replay of a September 30 directive and an October 3 firing is covered by tests.

## Current state and unknowns

Both definitions group normalized UTC-Z observation times before reading the
winning snapshot. Nanosecond precision is retained. They do not depend on
`NOW()` or dateTime casts. A timestamp with an offset, an invalid timestamp, an
undated observation or conflicting snapshots at the latest instant makes the
item unorderable; the queries abstain from showing it as current. This is a
supported-input boundary, not evidence that such an item is closed.

The historical addition of `kind` to an otherwise identical snapshot does not
create a conflict. The displayed JSON omits that redundant member; kind remains
its own column. Other differences at one instant remain ambiguous.

Owner comes from the same canonical tracker snapshot as status. `ownerState`
distinguishes `assigned`, `unassigned` (a recorded null) and `unknown`. An owner
with JSON escapes is unknown; the query never presents a partly decoded name.
Missing `about` links remain absent. A query cannot invent topic links for a
creator that did not supply them.

Each result is capped at 100 rows. A saturated result does not prove absence of
another item. Narrow the topic or inspect an exact WorkItem. The records plane
is explicit and configurable, so querying the wrong plane cannot quietly union
in unrelated historical work.

## Installation and reading

Load the JSON definitions through Quipu's authenticated query-library API after
normal source review. Use your configured endpoint; credentials stay in the
environment. Loading queries does not change tracker records or reaction state.

```sh
curl --fail "$QUIPU_SERVER/queries" \
  -H 'Content-Type: application/json' \
  -H 'X-Quipu-Client: agent-adhoc' \
  -H "Authorization: Bearer $QUIPU_AUTH_TOKEN" \
  --data-binary @queries/camayoc_open_plans.json
curl --fail "$QUIPU_SERVER/queries" \
  -H 'Content-Type: application/json' \
  -H 'X-Quipu-Client: agent-adhoc' \
  -H "Authorization: Bearer $QUIPU_AUTH_TOKEN" \
  --data-binary @queries/camayoc_lapsed_directives.json
```

Read through the provisioned Quipu `ask` tool:

```json
{
  "name": "camayoc_open_plans",
  "params": {
    "records": "https://camayoc.local/plane/crew/records",
    "topic": "review"
  }
}
```

```json
{
  "name": "camayoc_lapsed_directives",
  "params": {"records": "https://camayoc.local/plane/crew/records"}
}
```

Verify catalogue presence, execution under the served query budget, a positive
planned-item control and its owner/status/topic links. An empty lapse report
needs a positive firing control. Source tests also execute positive and negative
fixtures: old open versus latest closed, reopening, nanosecond ordering,
equivalent kind backfill, conflicting snapshots, missing clocks, owner unknowns,
and stale or unrelated firings. The fixture harness forbids network loading of
missing graphs, so an empty scope is an empty scope rather than a namespace fetch.

These queries add no timer or ingestion producer. The existing ingress and
review-due event path provide facts and firings. Creation-to-ingress timing and
scheduled delivery remain distinct operational acceptance checks.
