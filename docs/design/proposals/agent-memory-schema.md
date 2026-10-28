# Proposal: agent-memory schema for production quipu

Status: **proposed, not submitted.** Nothing in this directory has been sent to
quipu.svc. It answers `docs/design/agent-memory-entities.md` §7.1, which lists
what production needs before cy memory writes are admitted.

The bodies in `agent-memory/` are generated from
`shapes/agent-memory.shapes.ttl` by `scripts/memory_schema_proposal.py`.
`tests/test_memory_schema_proposal.py` fails when they go stale. Each JSON
file holds the exact arguments for `quipu_propose_schema_change` (REST:
`POST /propose`).

| File | Kind | Target (shapes name) | §7.1 item | Load when |
|---|---|---|---|---|
| `01-ontology.json` | ontology | `camayoc-agent-memory-vocabulary` | 1, new classes and terms | first |
| `02-shapes-new-classes.json` | shape | `camayoc-agent-memory` | 1, shapes for the new classes plus the record fields | after 01 |
| `03-shape-directive-scoped.json` | shape | `camayoc-agent-memory-directive` | 2, tighten `aegis:Directive` | after 02 |
| `04-shape-decision-scoped.json` | shape | `camayoc-agent-memory-decision` | 3, the Decision split | after quipu #911 is deployed and its data step is done |
| none needed | | | 4, plane graphs | already registered (see below) |
| `queries/mem_*.json` | stored queries | | 5, recall queries | after 03 (04 for the decision queries) |

## Census: the live aegis:Directive nodes (read-only, 2026-10-28)

Run with `quipu_query` against quipu.svc. These are the required fields of
`MemoryDirectiveShape`, checked one node at a time, plus `aegis:naturalKey`:

```sparql
PREFIX aegis: <http://aegis.gastown.local/ontology/>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
SELECT (COUNT(DISTINCT ?d) AS ?total)
 (SUM(?okRule) AS ?rule_ok) (SUM(?okIss) AS ?issuedBy_ok) (SUM(?okApp) AS ?appliesTo_ok) (SUM(?okTop) AS ?topic_ok)
 (SUM(?hasNK) AS ?has_naturalKey) (SUM(?all) AS ?conform_required)
WHERE {
 { SELECT ?d (IF(COUNT(DISTINCT ?r)=1 && SUM(IF(isLiteral(?r) && datatype(?r)=xsd:string,1,0))=COUNT(?r),1,0) AS ?okRule)
   WHERE { ?d a ?ty . FILTER(?ty = aegis:Directive) OPTIONAL { ?d aegis:rule ?r } } GROUP BY ?d }
 { SELECT ?d (IF(COUNT(DISTINCT ?i)=1 && SUM(IF(isIRI(?i),1,0))=COUNT(?i),1,0) AS ?okIss)
   WHERE { ?d a ?ty . FILTER(?ty = aegis:Directive) OPTIONAL { ?d aegis:issuedBy ?i } } GROUP BY ?d }
 { SELECT ?d (IF(COUNT(DISTINCT ?a)=1 && SUM(IF(isIRI(?a),1,0))=COUNT(?a),1,0) AS ?okApp)
   WHERE { ?d a ?ty . FILTER(?ty = aegis:Directive) OPTIONAL { ?d aegis:appliesTo ?a } } GROUP BY ?d }
 { SELECT ?d (IF(COUNT(DISTINCT ?t)=1 && SUM(IF(isIRI(?t),1,0))=COUNT(?t),1,0) AS ?okTop)
   WHERE { ?d a ?ty . FILTER(?ty = aegis:Directive) OPTIONAL { ?d aegis:topic ?t } } GROUP BY ?d }
 { SELECT ?d (IF(COUNT(?k)>0,1,0) AS ?hasNK)
   WHERE { ?d a ?ty . FILTER(?ty = aegis:Directive) OPTIONAL { ?d aegis:naturalKey ?k } } GROUP BY ?d }
 BIND(?okRule*?okIss*?okApp*?okTop AS ?all)
}
```

| | asserted `a aegis:Directive` | with RDFS subclasses (`?d a aegis:Directive`) |
|---|---|---|
| nodes | **923** | **1157** (adds OperationalRule, AlertRule, Policy, Procedure) |
| exactly one string `aegis:rule` | 3 | 3 |
| exactly one IRI `aegis:issuedBy` | 5 | 6 |
| exactly one IRI `aegis:appliesTo` | 0 | 0 |
| exactly one IRI `aegis:topic` | 0 | 0 |
| have `aegis:naturalKey` | 0 | 0 |
| **meet every required field** | **0** | **0** |

The shape's optional fields (`issuedOn`, `why`, `verbatim`) are not counted.
They can only add failures, and the result is already zero.

## Item 2 decision: scope the shape, don't migrate

A class-wide `sh:targetClass aegis:Directive` would put all 923 nodes, and the
234 subclass instances besides, into a state where any rewrite is refused. A
migration would have to invent `appliesTo` and `topic` for 923 free-form
records, none of which has them. That is fabricated data, so it is rejected.

`03` scopes the shape instead. It targets subjects of `aegis:naturalKey`, which
only cy memory records carry (0 live Directives have it), and applies the
Directive body only when the node is also an `aegis:Directive`:

```turtle
aegis:MemoryDirectiveShape a sh:NodeShape ;
    sh:targetSubjectsOf aegis:naturalKey ;
    sh:or ( [ sh:not [ sh:class aegis:Directive ] ] [ sh:node aegis:MemoryDirectiveBodyShape ] ) .
```

`04` scopes `MemoryDecisionShape` the same way. The live census found 50
`aegis:Decision` nodes, of which only 1 has `chose` (design §1.2), so the
Decision class has the same problem.

Checked with `quipu validate` (quipu 0.16.0) on hand-written fixtures:

| Data | 03 Directive | 04 Decision |
|---|---|---|
| legacy Directive or Decision, no naturalKey | valid | valid |
| complete memory record | valid | valid |
| memory record missing required fields | invalid | invalid |
| memory Preference (other class) | valid | valid |

**Known trade-off.** On a refusal, quipu 0.16.0 reports the `sh:or` as `OR not
satisfied` instead of the body's per-field `sh:message`. The per-field
messages are still in the body shape and in `shapes/agent-memory.shapes.ttl`,
which the create tools are generated from. If the quipu refusal must name
fields itself, the alternative is a marker subclass
(`aegis:MemoryDirective rdfs:subClassOf aegis:Directive`, targeted directly),
but cy would then have to write that type. That is a separate change.

## Item 4: plane graphs already exist

`quipu_graph_list` on quipu.svc (2026-10-28) shows all three planes already
registered and labelled (kind `knowledge`, trust chain
`https://camayoc.local/plane/chain/provenance`):

| Plane | Graph | trust_rank |
|---|---|---|
| crew:records | `https://camayoc.local/plane/crew/records` | 20 |
| crew:declared | `https://camayoc.local/plane/crew/declared` | 30 |
| crew:inferred | `https://camayoc.local/plane/crew/inferred` | 0 |

No proposal is needed. Re-run `QUIPU_SERVER=<prod> python3 scripts/planes.py
ensure` at load time anyway: it is idempotent and refuses if a label went
missing.

## Accept and load steps (for the accepter, in order)

Use the quipu MCP tools as the accepter's own identity, or the REST
equivalents with the bearer. Stop at the first refusal.

0. **Pre-checks (read-only).**
   - Confirm the census still holds: re-run the query above and require
     `has_naturalKey = 0`.
   - Confirm the source matches:
     `python3 scripts/memory_schema_proposal.py --check` passes, and
     `agent-memory/SOURCE_SHA256` matches `shapes/agent-memory.shapes.ttl`.
   - Look for property collisions:
     `quipu_shapes` / `quipu_load_ontology {"action":"list"}` must not show an
     `rdfs:domain` on any of the 25 properties in `01`. A domain would retype
     memory records, which is the `aegis:expiresAt` trap from design §7.
1. **Vocabulary (01).**
   - Submit: `quipu_propose_schema_change` with the fields of `01-ontology.json`.
   - Accept: `quipu_accept_proposal {"id": <id>, "decided_by": "<accepter>"}`.
     For an ontology proposal, accepting only records the decision.
   - Load:
     `quipu_load_ontology {"action":"load","name":"camayoc-agent-memory-vocabulary","turtle":<01.diff>}`.
   - Verify: `quipu_load_ontology {"action":"list"}` shows the name.
2. **New-class shapes (02).**
   - Submit the proposal, then accept it. Accepting a shape proposal parses the
     Turtle and writes it to the shapes table under the target name
     `camayoc-agent-memory`.
   - Verify: `quipu_shapes` lists `camayoc-agent-memory`, and
     `quipu_validate` with a complete Preference fixture conforms.
3. **Scoped Directive shape (03).**
   - Submit the proposal, then accept it.
   - Verify: `quipu_validate` on one live legacy Directive (any IRI from the
     census) still conforms, and a memory Directive missing `aegis:rule` is
     refused.
4. **Recall queries (item 5).** Load each file:
   `for q in queries/mem_*.json; do curl -sf -X POST "$QUIPU/queries" -H 'Content-Type: application/json' -H "Authorization: Bearer $T" --data-binary @"$q"; done`
   This is the same loop as `scripts/bootstrap.sh`. Hold
   `mem_decisions_current` and `mem_decision_history` until step 6.
5. **quipu GateApproval split.** Merge and deploy quipu #911, then add the
   data triple `aegis:GateApproval rdfs:subClassOf aegis:DecisionRecord` on
   quipu.svc (the post-merge step in that PR).
6. **Scoped Decision shape (04).**
   - Submit the proposal, then accept it.
   - Verify: a complete memory Decision conforms under both the governance
     shapes and `camayoc-agent-memory-decision`, and a legacy `aegis:Decision`
     still conforms.
   - Then load the two decision recall queries.
7. **Production writes.** Run `cy` against quipu.svc once with each create
   tool, and read each record back with its `mem_*` query.

Rollback: shapes load by name, so
`POST /shapes {"action":"remove","name":<target>}` removes one set without
touching the others. `quipu_load_ontology {"action":"remove"}` does the same
for the vocabulary. No live data is rewritten at any step.
