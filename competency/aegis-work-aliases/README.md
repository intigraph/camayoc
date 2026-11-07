# Raw stored-data alias acceptance gates

These read-only queries express a **migration acceptance condition**, not a
claim that production already satisfies it or permission to write legacy types.
Run them against a raw RDF snapshot or lab reader with query compatibility
lowering disabled. Select an explicit dataset containing the pinned Camayoc
class declarations and Seeds work-item project records; do not silently broaden
to all registered graphs. Executing them through a reader that rewrites WorkItem/Bead
into Action cannot establish raw alias coverage.

`canonical.rq` is the non-empty Action population control. WorkItem and Bead
views follow the existing raw `dcterms:isReplacedBy` and `rdfs:subClassOf`
class declarations to the canonical Action population. They do not require
duplicate deprecated type assertions on canonical writes. The Vtodo view follows the existing `skos:exactMatch` relation in either
direction and returns Action IDs, excluding self-links. Calendar IDs remain
distinct; no `owl:sameAs`, class-equivalence assertion or reasoning is added.
The deprecated class mappings stay in `ontology/core.ttl`; Seeds retains profile
ownership through `shapes/aegis/seeds-profile.json`.

Acceptance requires non-empty, untruncated, complete, identical item sets for
all four queries on the same snapshot. Record snapshot identity, dataset scope,
query bytes and complete answers. A difference is a migration blocker to
investigate, **not** authorization to backfill legacy types or weaken the gate.
The executable controls in `tests/test_aegis_work_aliases.py` demonstrate canonical-only writes, missing/wrong class mappings,
absent/wrong/self calendar links, reverse links, multiple items and empty input. They do not prove production completeness, endpoint raw-read behavior or
stored-data revalidation.
