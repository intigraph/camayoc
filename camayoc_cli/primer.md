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
| `cy flush` | Replay records queued while Quipu was unreachable |
| `cy prime [--brief] [--json] [--static]` | This primer plus live state |
| `cy record <entity> --help` | Every flag of one entity, from its shape |

## MCP tools

<!-- BEGIN generated mcp-tools -->
None yet.
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
- When Quipu is unreachable a record is queued and `cy flush` replays it.

## Next steps

- Design: `docs/design/agent-memory-entities.md` in the camayoc repo
- Skill: `skills/camayoc/SKILL.md`
- Help: `cy --help`, `cy record <entity> --help`
