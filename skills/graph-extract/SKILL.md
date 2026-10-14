---
name: graph-extract
description: >-
  Extract entities and relationships from source material (text, docs, code, issues, PDFs) and
  ingest them into a Quipu knowledge graph as a structured episode. Portable: works from any LLM
  agent — it only needs shell and HTTP, no framework. Triggers on "extract to graph",
  "ingest into quipu", "build knowledge graph from", "graph-extract", "add this to the ontology",
  or when asked to capture knowledge from a document/issue/repo into the graph. Auto-detects two
  modes: a pre-specified ingest request (entities/relationships already listed) or raw source
  material to extract from scratch.
allowed-tools:
  - Bash
  - Read
  - Glob
  - Grep
  - WebFetch
---

# graph-extract — source material → Quipu knowledge graph

The portable safety invariants are canonicalized in
`references/portable-safety-contract.json`. That contract is shared byte-for-byte with the
executing Aegis skill and checked on the fleet's scheduled skill-selfheal path; update both copies
together. Environment-specific scripts and operating detail may differ, but these invariants may not.

This skill is the portable, LLM-agnostic way to get knowledge into a graph. **You** (the agent) do
the extraction — the cheap, mechanical part — and POST a structured episode to Quipu's HTTP API. No
pipeline, no worker pool, no job runner. It runs anywhere there's a shell and network access to the
graph.

**Stack tools this reaches for**

| Present | This skill uses it for | Absent — what happens |
|---|---|---|
| **[Quipu](https://github.com/scbrown/quipu)** at `${GRAPH_URL}` | **required.** `POST /episode` is the entire write path | nothing is ingested. Save the assembled nodes/edges and retry — never report a write you did not observe |
| **[quipu](../quipu/SKILL.md)** skill | checking whether an entity already exists, so facts attach instead of forking a duplicate | you will create a second node for a thing that already had one — see that skill's Limit (a) |
| **[bobbin](https://github.com/scbrown/bobbin)** | reading the source material when it is a codebase rather than a document | `Read`/`Glob`/`Grep`, which is fine for a file and poor for a repo |

**This skill requires a running Quipu endpoint.** It writes; there is no offline
mode. If the graph is unreachable, the correct outcome is an explicit failure and
a saved payload, not a shrug — see Failure Modes.

**Skill resources:** the verified episode schema and the entity/relationship taxonomy are in
`{baseDir}/references/episode-schema.md` and `{baseDir}/references/taxonomy.md`. Read them before
your first POST.

**Graph endpoint:** `${GRAPH_URL}/episode` (group `${GRAPH_GROUP}`). Set it to a **hostname**,
never a raw private IP — IPs move, and a hardcoded one turns a portable skill into yours only.

**Write auth (if the graph requires it):** some deployments gate **writes** behind a bearer token
while **reads stay open**. If yours does, set `GRAPH_TOKEN` to the write token — the `POST` below
sends it only when set, so it stays a no-op on an open graph. A bare-curl write against an
auth-enabled graph silently `401`s (see Failure Modes).

## Essential Principles

1. **One fact per edge, no speculation.** Every edge is `{source, target, relation}` — all three
   required. Only assert what the source material actually states. If you're guessing, tag it (see
   confidence, principle 5) rather than inventing a clean-looking fact.

2. **Name entities concretely and canonically.** `node01`, not "the host". `search-api`, not "the
   search service". Reuse names already in the graph so facts attach to existing entities instead
   of forking duplicates — query first (`/query`) when unsure, or use Quipu's resolve step.

3. **Branch on `outcome`, NEVER on `count`.** A successful POST returns HTTP 200 and an
   `outcome` of `created`, `updated` **or `unchanged`** — all three are success.

   > ⛔ **`count` CANNOT answer "did this land".** It is the number of triples written by THIS
   > transaction, not facts present. The endpoint is idempotent on a content hash, so a
   > byte-identical re-post writes nothing and returns `outcome: unchanged, tx_id: 0, count: 0` —
   > the SAME response shape as a write that achieved nothing. `tx_id: 0` is a sentinel meaning
   > no transaction was opened; it is not a transaction id.
   >
   > This skill previously said to treat `count: 0` as a FAILURE, and that is a **pipeline from a
   > reporting bug into a corruption bug**: a correct idempotent re-post reads as failed, so the
   > source is not marked ingested, so the next pass ingests it again — and a second episode about
   > the same subject by a different author is not byte-identical, which either forks the entity
   > under a re-worded name or revises a reused node description. Preserve identity and omit descriptions on reuse.

   Do not trust the optional SPARQL regex self-check — `regex(str(?l))` FILTERs are unreliable
   here. Verify with the control-gated read-back in step 4 instead.

4. **Minimum viable episode: ≥2 nodes and ≥1 edge.** If you can't extract that much that's real,
   skip — don't pad the graph with trivia.

5. **Tag uncertainty.** When the store supports a `confidence` qualifier on edges, mark each one
   `EXTRACTED` (explicitly stated, e.g. a config line), `INFERRED` (a reasonable deduction), or
   `AMBIGUOUS` (uncertain — flagged for human review). Auto-extracted facts SHOULD carry a tag.

## Workflow

### 1. Detect mode and gather source material
- **Pre-spec mode** — the request already lists `ENTITIES:` / `RELATIONSHIPS:` and names source
  issue(s) ("extract knowledge from <SRC>"). Read each SRC fully; the listed entities/relationships
  are your backbone — your job is to structure them and write accurate descriptions.
- **Raw mode** — you're handed a document, file, repo path, or issue. THAT is the source. Read it in
  full (`Read`/`Glob`/`Grep` for files; `WebFetch` for URLs; your tracker's show command for issues).
  Follow references inside it (linked issues, commit hashes, file paths) one hop.

### 2. Extract nodes and edges
Map the material onto the taxonomy in `{baseDir}/references/taxonomy.md`:
- **Nodes:** `{name, type, description}` for new nodes; `{name, type}` for reuse.
  Keep a governed type on every node; omit description on reuse.
- **Edges:** `{source, target, relation}` — `relation` from the taxonomy's relationship vocabulary;
  `source`/`target` are node names.
- Prefer results and learnings (what was done, who did it, what was discovered) over restating the
  task. For dated events use `deployed_on` / temporal relations.

### 3. POST the episode
See `{baseDir}/references/episode-schema.md` for the exact JSON shape. Skeleton:

```bash
curl -s -m 20 -w '\nHTTP %{http_code}\n' ${GRAPH_URL}/episode -X POST \
  -H 'Content-Type: application/json' \
  ${GRAPH_TOKEN:+-H "Authorization: Bearer $GRAPH_TOKEN"} \
  -d '{
    "name": "<episode-name>",
    "episode_body": "<short factual paragraph of the source>",
    "source": "graph-extract",
    "group_id": "${GRAPH_GROUP}",
    "nodes": [ {"name":"...","type":"...","description":"..."} ],
    "edges": [ {"source":"...","target":"...","relation":"..."} ]
  }'
```

Episode `name`: `ingest-<src-id>` for a pre-spec'd source, or `<topic>-<date>` for raw material.

### 4. Confirm with a CONTROL, then annotate the source

A landed write is not a findable one, and the obvious check lies in the reassuring direction —
during load, a read for your own node can return nothing for a write that fully succeeded. So
prove the instrument works BEFORE believing an absence:

```bash
# 1. CONTROL — this MUST return rows before any absence means anything
curl -s "${GRAPH_URL}/query" -X POST -H 'Content-Type: application/json' \
  -d '{"query":"SELECT ?s WHERE { ?s a <'"${GRAPH_NS}"'Directive> } LIMIT 3"}'
# 2. ONLY THEN look for your own node. A zero before step 1 passes proves NOTHING.
```

- Ask for the node **the way a reader would ask** — by the `type` they would filter on. A node
  that is present but not retrievable on that path is ingested and invisible.
- Label the source `ontology-ingested` on an OBSERVED 200 whose `outcome` is `created`,
  `updated` or `unchanged` — never on `count`, and never on "it was dispatched".

> ⚠️ **A timeout, an empty body, or an explicit `502` is NOT a failed write.** The response can be
> lost at or after commit, so all of those — and a read-back showing nothing — are consistent with
> a write that succeeded. Do not retry on sight: run the control, read twice with a gap, and if you
> do retry, **re-send the SAME BODY byte-for-byte.** The impulse to improve the wording on a retry
> is exactly how a correctly-followed retry rule produces the duplicate it exists to prevent.

## Failure Modes

| Situation | Action |
|-----------|--------|
| `${GRAPH_URL}` unreachable, or a non-200 that is **not** an indeterminate transport result | Do NOT mark the source ingested. Save the assembled `nodes`/`edges` (note: `KNOWLEDGE-PENDING-INGESTION`) so a retry re-runs cleanly. |
| `count: 0` / `tx_id: 0` with `outcome: unchanged` | The content hash already exists. Mark ingested only after control-gated reader-path verification confirms the facts. |
| Timeout, empty body, gateway failure, or HTTP `408` (including a zero-duration timeout) | **INDETERMINATE, not failed.** Run the control query, then read twice with a 15-second gap. Only two absences permit ONE retry with the identical body; then verify `rdfs:comment` count is 1, not merely that the node is present. |
| Same node needs a corrected description | Omit description on reuse. Current servers revise attributed descriptions and may refuse ambiguous legacy comments. For a deliberate correction use `/set`, then verify the value and count. |
| **`401 Unauthorized`** on the `POST` | The graph gates writes behind a bearer (reads stay open) and none/an invalid one was sent — auth, NOT an outage. Set `GRAPH_TOKEN` to the write token and retry. A `401` on write while `/query` reads still `200` is the tell: it is auth, not a wedge. |
| < 2 nodes or < 1 edge extractable | Skip — nothing knowledge-worthy. Say so explicitly. |
| Referenced source missing | Ingest what's available; don't fail the whole run. |
| SHACL validation rejects the write (400) | Read the violation (focus node / path / message), fix the node `type` or a required property, retry. |

## Portability notes

This skill depends only on `curl` + the Quipu HTTP contract. To point it at a different graph,
change the endpoint. To run it from a non-Claude agent, the only Claude-specific piece is the skill
wrapper — the workflow (read source → extract nodes/edges → POST) is plain instructions any capable
LLM can follow. The capability travels with the prompt, not with a platform.
