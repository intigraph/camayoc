# Quipu /episode schema (VERIFIED against quipu source + live round-trip)

The episode JSON shape is defined by `quipu/src/episode/mod.rs`. Use EXACTLY these field names —
unknown fields are silently dropped, and missing required edge fields cause an HTTP 400.

```json
{
  "name": "<episode-name>",
  "episode_body": "<short factual paragraph of source material>",
  "source": "graph-extract",
  "group_id": "${GRAPH_GROUP}",
  "nodes": [
    {"name": "<EntityName>", "type": "<EntityLabel>", "description": "<one-line fact>",
     "properties": {"<key>": "<value>"}}
  ],
  "edges": [
    {"source": "<EntityName>", "target": "<EntityName>", "relation": "<relationship>"}
  ]
}
```

- **Node fields:** `name` (required), `type`, `description`, optional `properties` (JSON map).
  NOT `labels[]` / `summary`.
- **Edge fields:** `source`, `target`, `relation` — ALL required. NOT
  `name` / `fact` / `source_node_name` / `target_node_name`.
- **Episode source field** is `source` (not `source_description`).
- `group_id` is provenance/namespacing (best-effort, not hard isolation).

## Success / failure signal

- Success: **HTTP 200** with a JSON body containing `count` (triples written, must be > 0),
  `episode` (the name), and `tx_id`.
- `count: 0` or any non-200 → the ingest did NOT take. Treat as failure.
- Do NOT rely on a SPARQL `regex(str(?l))` self-check to confirm — that FILTER is unreliable on
  this deployment. The `count` + `tx_id` in the POST response is the authoritative signal.

## Lookalike format — do NOT use

Graphiti-style extraction guides document a different schema
(`labels` / `summary` / `fact` / `source_node_name` / `target_node_name`), and an LLM that has seen
one will reach for it. Quipu REJECTS that with HTTP 400 ("missing field source"). Use the entity
LABELS and RELATIONSHIP vocabulary from the taxonomy, but the JSON SHAPE above.

## Forward-looking: confidence qualifier

When the store adds a per-edge `confidence` qualifier (tracked feature), include it on each edge:

```json
{"source": "...", "target": "...", "relation": "...", "confidence": "EXTRACTED|INFERRED|AMBIGUOUS"}
```

Until then, if uncertainty matters, note it in the node `description` or the `episode_body`.
