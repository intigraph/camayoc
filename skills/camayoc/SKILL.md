---
name: camayoc
description: Use governed memory backed by a quipu knowledge store. Load this when working in a project that has (or should have) a quipu — to query prior decisions and conventions before re-deciding them, to record decisions/work/outcomes at the moment they happen, to bootstrap a new store with the camayoc ontology and shapes, or when the user mentions quipu, camayoc, knowledge graph memory, "what did we decide", or recording a decision.
---

# camayoc — the knot-keeper's skill

You are working with governed memory: a quipu store where facts carry
provenance tags and SHACL refuses writes that lack them. This skill teaches
the four moves. The shapes enforce the rules whether or not you follow the
guidance — a refused write means the gate is working, not that you should
route around it.

**Server:** resolve in this order: `$QUIPU_SERVER` → project config
(`env.json`, then `.bobbin/config.toml` `[quipu.server] bind`) → `http://localhost:3030`.
A server reached only through that default is UNVERIFIED: any process can hold
the port, so it is not your governed memory until you configure it. Reads are
open; writes need `Authorization: Bearer $QUIPU_AUTH_TOKEN` when the server
is gated. Never hardcode a server or namespace — they are deployment
parameters.

## Move 0 — Bootstrap (only when the store is bare)

1. Probe: `GET /health`, then `GET /stats`. Unreachable is NOT "no
   knowledge" — say you could not look; do not proceed as if empty.
2. Check shapes: `POST /shapes {"action":"list"}`. If camayoc shapes are
   missing, load `ontology/*.ttl` then `shapes/*.shapes.ttl` from this repo
   via `POST /shapes {"action":"load","name":...,"turtle":...}` (idempotent).
3. **Prove the gate**: attempt one deliberately untagged probe write and
   confirm it is REFUSED; then write and retract a correctly tagged probe.
   A store that accepts the untagged probe has no gate — report that, do
   not ingest into it.

## Move 1 — Query first

Before deciding anything a past session may have decided, ask. The
competency questions in `competency/` are the canonical set.

**Map before you write SPARQL.** Call the `map_question` tool (jev MCP
server, shipped with this plugin) with the question as you would phrase it;
from a shell, `python3 scripts/competency.py --map "<question>"` returns
the same verdict as one line of JSON. Act on `outcome`:

- `mapped` — the question is an instance of `competency_id`. If
  `stored_query.state` is `STORED`, run that named query and fill its
  parameters. Any other state (`UNWRITTEN`, `GAP`, ...) is the reason there
  is no query yet; fall back to hand-written SPARQL and say so.
- `abstained` — no competency question fits. File the asked question as a
  CANDIDATE competency question; that is how the suite grows from what
  agents actually ask. Then answer it by hand.
- `human_reads` — confidence was below the 0.75 floor. `candidate` is NOT
  an answer; do not run its query as if it were. Answer by hand.

A mapping is a model judgment: anything you record from it carries
`sourceKind "inferred"`, never promoted. When the tool is unavailable (no
key), it errors; that is not a mapping, so go straight to SPARQL.

Hand-written SPARQL goes through `POST /query`; the patterns:

- *What did we decide about X, and why?* — find `camayoc:Decision` nodes
  whose `about`/`decidedIn` reaches X; read `chose`, `over`, `rationale`.
- *What conventions apply here?* — `sourceKind "declared"` facts in scope.
- *Prior work on this area?* — work items linked to the entity, with
  outcomes.

Respect the tags in what comes back: a fact tagged `inferred` is a lead,
not a law. If two sources conflict, the conflict is the answer — report it
as contested; do not silently pick.

## Retain directives before traceability evaluation

A directive needs a typed WorkItem `trackedBy` link and a typed Policy
`governedBy` link, or one explicitly typed
`https://w3id.org/intigraph/quechua/ns#traceabilityDueAt` `xsd:dateTime`.
Use the date to retain a decision whose policy/work-item evaluation is deferred.
Suggest fourteen days from capture, then choose and supply the actual UTC date;
never silently manufacture links or drop the decision after a refusal. The date
is an evaluation deadline, not an expiry or authority to execute the directive.

Check that the receiving store has adopted this Quechua term and shape first.
An untyped JSON date string does not satisfy the datatype constraint: use the
governed writer's typed literal representation. Do not invent a `cy` flag if the
installed CLI does not expose it. Missing both links and date remains refused.

The read-time query `camayoc_overdue_directive_traceability` takes `declared`
(the configured trusted plane) and `now` (a timezone-bearing instant). More than
100 rows means incomplete coverage. The installed `directive-lapse` adapter
schedules the explicit deadline and sends overdue directives through the
existing administrator alert path; publishing the query alone is not adoption.

## Move 2 — Record at the moment

When a decision is made **in this session**, record it now — not in a
wrap-up summary. One episode per decision or small batch, via
`POST /episode` (idempotent — branch on `outcome`, never on `count`;
`unchanged` is success):

```json
{
  "name": "decision-<short-slug>",
  "source": "<work-item or session ref>",
  "nodes": [
    {"name": "<decision-slug>", "type": "Decision",
     "properties": {"chose": "...", "over": "...", "rationale": "...",
                     "sourceKind": "declared|inferred", "ts": "<iso8601>"}}
  ],
  "edges": [
    {"source": "<decision-slug>", "target": "<work-item>", "relation": "decidedIn"}
  ]
}
```

Likewise work items (created/closed, with `ts`) and outcomes
(`done|abandoned|superseded|failed`) at session end. Reuse existing node
names **byte-for-byte** — a re-worded name mints a duplicate entity.

## Move 3 — Tag honestly

`sourceKind` is mandatory and means exactly:

- **`observed`** — a record exists and you (or a parser) read it. Cite it.
- **`declared`** — a human said it, in this session or a document. Quote or
  reference them.
- **`inferred`** — you concluded it. It goes to the inferred plane, low
  trust, promotable later by someone with authority — never by you
  up-tagging it.

Never claim `observed` or `declared` for your own conclusion. The gate will
not catch a plausible lie about source — only you can. That honesty is the
entire value of the record to the next reader.

## Never

- Write to a governed plane without the tags (the store refuses; do not
  work around a refusal — report it).
- Store a judgment that decays (liveness, "currently in progress",
  "latest"). Store facts true at write time; derive status by query.
- Treat "could not reach quipu" as "no knowledge exists". Different answers.
- Summarize a session's decisions from memory at the end instead of
  recording them when they happened. Late recall is `inferred`; in-the-moment
  recording is `declared`.
