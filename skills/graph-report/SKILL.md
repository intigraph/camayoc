---
name: graph-report
description: >-
  Produce an orientation report over a Quipu knowledge graph — its size, the most central
  entities ("god nodes"), recent activity, suggested questions, and (when available) surprising
  cross-community connections. Portable: shell + HTTP to ${GRAPH_URL} only, runs from any LLM agent.
  Triggers on "graph report", "summarize the knowledge graph", "what's in the ontology",
  "orient me on the graph", "graph-report", "what are the key entities", or when someone needs a
  map of the graph before diving in. It is a static GRAPH_REPORT.md equivalent, but computed live.
allowed-tools:
  - Bash
  - Read
---

# graph-report — orient on the Quipu knowledge graph

Graph-building tools hand you a `GRAPH_REPORT.md` (god nodes, surprises, suggested questions) once,
at build time — and it is stale the next day. This skill produces the same orientation **live** from
Quipu, over HTTP: no static file, no framework. It pairs with [graph-extract] (which fills the graph).

**Stack tools this reaches for**

| Present | This skill uses it for | Absent — what happens |
|---|---|---|
| **[Quipu](https://github.com/scbrown/quipu)** at `${GRAPH_URL}` | **required.** `/stats`, `/project` (PageRank), `/query` | there is no report. Nothing here can be approximated |
| **[quipu](../quipu/SKILL.md)** skill | the follow-up queries this report is designed to hand you | the report is a dead end instead of a starting point |
| **[graph-extract](../graph-extract/SKILL.md)** | filling a graph thin enough that PageRank is noise | report what's there and say it's small |

**This skill requires a running Quipu endpoint** with the graph algorithm
endpoint (`/project`) available. If `/project` is missing, report size from
`/stats` alone and **name the sections you could not compute** — an orientation
report with silently-omitted sections is worse than a short one.

**Graph endpoint:** `${GRAPH_URL}` (group `${GRAPH_GROUP}`). See
`{baseDir}/references/endpoints.md` for the exact request shapes and the schema-node filter list.

## Essential Principles

1. **Filter out schema AND episode nodes or the report is useless.** Raw PageRank ranks three kinds
   of structural noise at the top: (a) the ontology *class* nodes (`LXCContainer`, `WebApplication`,
   …) — every instance `rdf:type`-points to them; (b) the `prov:` / `rdf:` / `rdfs:` / `owl:` / `sh:`
   / `dcterms:` namespaces; and (c) **the episode nodes themselves** — each episode is a
   `prov:Activity` and every fact it wrote points back to it via `prov:wasGeneratedBy`, so episodes
   dominate centrality. Exclude all three (classes via the `rdf:type`-object query; episodes via
   `?a a prov:Activity`). What remains are the real domain hubs. *(Without the episode exclusion, a
   young graph's "god nodes" are just a list of its own episodes — verified.)*

2. **Report what's there, name it concretely, don't editorialize.** Pull real entities and counts;
   present them plainly. If a section's data source isn't available yet, say so — don't fabricate a
   "surprising connection" to fill space.

3. **Every hub becomes a question.** The point of orientation is the next query. For each top hub,
   emit a concrete follow-up ("What runs on `node01`?", "What depends on `search-api`?").

## Workflow

### 1. Overview — graph size
```bash
curl -s ${GRAPH_URL}/stats        # {entities, facts, predicates}
```

### 2. God nodes — central domain entities
- Get raw centrality: `POST /project {"algorithm":"pagerank","max_iters":20,"persist":false}`.
- Get the exclusion sets: classes `{"query":"SELECT DISTINCT ?c WHERE { ?x a ?c }"}` and episodes
  `{"query":"PREFIX prov:<http://www.w3.org/ns/prov#> SELECT ?a WHERE { ?a a prov:Activity }"}`.
- Drop any result in the class set, the episode set, or a schema namespace (see
  `{baseDir}/references/endpoints.md`). The top ~10 survivors are your god nodes.
- **At current graph scale, complement PageRank with in-degree** — on a young, episode-dense graph
  PageRank is flat across domain nodes, so also rank by incoming domain edges (query in
  endpoints.md) and merge. This surfaces referenced infrastructure (e.g. a busy host) that PageRank
  alone buries under episode noise.
- For each hub, fetch its label/type for a readable name and its neighbourhood (endpoints.md).

### 3. Recent activity — what was ingested lately
The graph's changelog is its episodes. **Caveat (quipu 0.3.0):** episodes do NOT carry a structured
`prov:atTime` — the date lives inside the `rdfs:comment` text. So you can't `ORDER BY` time in
SPARQL. Options, best-first:
- If `GET /transactions` is available, use it for true commit order.
- Else list episodes and read the dates from their comments:
  `POST /query {"query":"PREFIX prov:<http://www.w3.org/ns/prov#> PREFIX rdfs:<http://www.w3.org/2000/01/rdf-schema#> SELECT ?l ?c WHERE { ?a a prov:Activity ; rdfs:label ?l ; rdfs:comment ?c }"}`
  and sort client-side on the leading `YYYY-MM-DD` in each comment.
- A server-side `/report` endpoint will eventually expose proper recency; until then this is best-effort.

### 4. Surprising connections
- **When community detection is available** (quipu `quipu:memberOfCommunity` facts): rank edges whose endpoints sit in *different* communities and whose predicate is *rare* between
  those communities — those are the anomalies worth a human's eye.
- **v1 fallback (today, no communities):** surface entities that participate in an unusually *wide
  variety of predicates* (a node tying together many relation types is a structural bridge). Query
  predicate-diversity per entity and report the top few. Clearly label this as the interim heuristic.

### 5. Suggested questions
Generate 5–8 from the god nodes + their types, e.g. for a `ProxmoxNode` hub → "What runs_on it?";
for a `WebApplication` hub → "What does it depend_on / route_to?"; for a `Directive` → "What
applies_to it?". These are ready-to-run `/query` follow-ups.

### 6. Assemble
Emit a short markdown report: **Overview · God nodes · Recent activity · Surprising connections ·
Suggested questions.** Keep it scannable — this is a map, not a dump.

## Failure Modes

| Situation | Action |
|-----------|--------|
| `/project` or `/query` non-200 | Report size from `/stats` alone; note which sections are unavailable. |
| Graph tiny (< ~20 entities) | Skip PageRank; just list entities by type. PageRank is noise at that scale. |
| Everything in top-PageRank is a class/prov node | Your schema filter isn't applied — re-check step 2. |
| SPARQL `regex(str(?l))` returns nothing | Known-unreliable FILTER on this Quipu; query by structure (predicates/types), not label regex. |

## Portability notes

Depends only on `curl` + the Quipu HTTP API. A server-side `/report` endpoint may eventually
compute god-nodes/surprises/questions in one call; until then this skill composes the existing
`/stats` + `/project` + `/query` endpoints. Point `${GRAPH_URL}` elsewhere to report on a different
graph.
