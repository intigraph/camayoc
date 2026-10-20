# Staged domain profiles

These files transfer domain ownership from Quipu to Camayoc. They are source
candidates, with no automatic load, schedule or data rewrite. `placement.json`
records every source shape and the pinned Quipu and Quechua inputs.

- `homelab-infra.shapes.ttl`: hosts, containers, networking, services and storage.
- `personal-media.shapes.ttl`: accounts, libraries, people and entertainment.
- `software-factory.shapes.ttl`: crew, code production and shared work relations.
- `public-term-profiles.shapes.ttl`: existing DROP decisions target their public
  replacements, rather than adding homelab terms to Quechua.
- `shared-vocabulary.ttl`: unchanged declarations shared across constraints.

The test fixture `tests/fixtures/aegis-upstream/legacy-compatibility.shapes.ttl`
preserves the exact original bytes for a staged
load-before-remove transition. It is not a new canonical work-item profile.
Reversing only declared public-target substitutions and recombining the split
graphs, including the upstream KEEP-only test fixture, is RDF-isomorphic to that original.
KEEP shapes remain upstream in Quipu or Quechua governance; the fixture grants
Camayoc no ownership or loading authority for them. This is source equivalence, not a
claim that the new public targets already validate the production graph.

Work items use **Seeds**, whose authoritative source is referenced by exact
commit/path/hash in `seeds-profile.json`, not copied into this repository.
`schema:Action` is canonical. An iCalendar `Vtodo` is a separate node linked by
`skos:exactMatch`; it is not an identical IRI. Historical Bead/WorkItem names are
deprecated compatibility vocabulary. Camayoc's core WorkItem gate is retired;
the Seeds profile owns tracker validation and the governance terms on Actions.
Existing legacy-reader code does not authorize new legacy writes.

`competency/aegis-domain-classes.json` supplies executable class/label inventory
questions. Each runs against a positive fixture and a missing-label negative.
Those controls establish CQ coverage, not the full semantic coverage of every
domain question. The original constraints remain subject to the engine tests.

The copied property and rule files retain original bytes. In particular, the
property file documents domain-inference consequences and the transitive rules
are not automatically loaded. Review their per-domain split and retained engine
claims before any activation; no new inference is enabled here.

Rollout requires reviewed released artifacts, an admitted Gold pin candidate,
same-SHA rehearsal/apply, unchanged loaded-set count, no new data violations and
alias-query proof. Load destination bytes before removing Quipu source copies.
Retain prior exact pins/artifacts for the scoped rollback. No live unload is
part of this source change.

## Existing CI entrypoint

The existing just test job executes profile-source and domain-pack controls.
The profile helper resolves the Seeds-owned bytes at the manifest's immutable
commit and checks their SHA256 before use. CI uses that public read path; local
checks may supply CAMAYOC_SEEDS_SOURCE with the same checked bytes. Missing,
changed or unavailable source fails instead of selecting another version.
No workflow change or additional credential is required.
