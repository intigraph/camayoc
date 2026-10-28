# Camayoc agent primer

## What camayoc is

Camayoc owns what facts mean in a Quipu store: the ontology, the SHACL shapes
and the stored queries. `cy` (alias of `camayoc`) is its agent-memory tool:
one purpose-built command per memory entity instead of free-form memory files.
Every write goes through Quipu's shapes, and a refusal names the missing field
in plain words. Recall comes back through stored queries that drop superseded
and expired records.

## Agent workflow

1. Recall before deciding: `cy recall decision --about <entity>` or
   `cy recall directive --applies-to <agent>`.
2. When a human rules, record it at once: `cy record decision --chose ... --by ... --about ... --topic ...`.
3. A new record on the same natural key supersedes the old one; never delete.
4. Unknown topic? Register it: `cy topic add <name>`, then record.
5. Never put a secret in a record. A Reference stores the secret-store key name in `--secret-ref`.
6. Before you stop, write your handoff: `cy record handoff --agent <you> --next "..."`.

## Core commands

| Command | Use |
| --- | --- |
| `cy record <entity> --<field> ...` | Write one entity; refuses in plain words when a field is missing |
| `cy recall <entity> [filters]` | Current records, declared first, at most 50 |
| `cy recall decision --history <key>` | Every version of one decision key |
| `cy topic add <name> [--alias ...]` | Register a memory topic (a natural-key part) |
| `cy flush` | Replay the local FIFO; keep any unconfirmed head and its tail |
| `cy flush --hook` | Bounded replay for hooks; always exits 0 |
| `cy backlog` | Read the local queued count as JSON; unknown is null |
| `cy mcp` | Serve the same tools over MCP stdio (`record_<entity>`, `recall_<entity>`) |
| `cy prime [--brief] [--json] [--static]` | This primer plus live state |
| `cy record <entity> --help` | Every flag of one entity, from its shape |

## MCP tools

<!-- BEGIN generated mcp-tools -->
Served by `cy mcp` (server name `camayoc-memory`).

| Tool | Use |
| --- | --- |
| `record_decision` | Record one ruling on a scoped question: what was chosen, by whom, about what. Refuses in plain words when a field is missing. |
| `recall_decision` | Current decisions, optionally about one entity and topic. |
| `recall_decision_history` | Every version of one decision key, newest first, superseded ones included. |
| `record_directive` | Record a standing rule: one imperative sentence, who issued it, who it binds. Refuses in plain words when a field is missing. |
| `recall_directive` | Current directives binding one agent, role or repo, plus crew-wide ones. |
| `record_handoff` | Write the agent's one current handoff; each write supersedes the last. Refuses in plain words when a field is missing. |
| `recall_handoff` | The agent's current handoff, if younger than its max age. |
| `record_preference` | Record how a person wants something done, in one context. Refuses in plain words when a field is missing. |
| `recall_preference` | Current preferences of one person, optionally in one context. |
| `record_reference` | Record where a system lives and how to reach it. Key names only, never secrets. Refuses in plain words when a field is missing. |
| `recall_reference` | Current references, optionally into one system and for one topic. |
<!-- END generated mcp-tools -->

## Memory entities

<!-- BEGIN generated entities -->
| Entity | Record | Required flags | Natural key | Recall |
| --- | --- | --- | --- | --- |
| decision | `cy record decision` | `--chose` `--by` `--about` `--topic` | about + topic | `cy recall decision [--about] [--topic]`; `cy recall decision --history <key>` |
| directive | `cy record directive` | `--rule` `--issued-by` `--applies-to` `--topic` | applies-to + topic | `cy recall directive [--applies-to]` |
| handoff | `cy record handoff` | `--agent` `--next` | agent | `cy recall handoff [--agent]` |
| preference | `cy record preference` | `--person` `--prefers` `--context` `--topic` | person + topic | `cy recall preference [--person] [--context]` |
| reference | `cy record reference` | `--subject` `--topic` `--locator` | subject + topic | `cy recall reference [--subject] [--topic]` |
<!-- END generated entities -->

## Writing safely

- Writes route to a plane by sourceKind: declared (a human said it), observed
  (measured), inferred (an agent concluded it). Inferred never overrides declared.
- Replacing a ruling by someone else needs `--supersede <iri>`.
- When Quipu is unreachable a record is queued and `cy flush` replays it in order.
- Queued is pending, never confirmed. Bobbin reports the local backlog without reading Quipu.
- A refused head stays queued; inspect it before retrying. See `docs/offline-memory.md`.

## Next steps

- Design: `docs/design/agent-memory-entities.md` in the camayoc repo
- Skill: `skills/camayoc-memory/SKILL.md` (Claude) and `.agents/skills/camayoc-memory/SKILL.md` (Codex)
- Help: `cy --help`, `cy record <entity> --help`, `man docs/man/cy.1`
