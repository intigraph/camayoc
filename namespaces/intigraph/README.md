# Intigraph namespace migration sources

`mapping.json` freezes the 1,254 measured old/new IRI pairs published through
`https://w3id.org/intigraph/`. The document URLs and SHA-256 hashes identify the
measured publication. The 39 remaining declarations stay in their legacy
namespaces; this first stage assigns them no new owner or identifier.

Build additive Camayoc source candidates offline:

```sh
python3 scripts/build_stable_namespaces.py --output /path/to/new/bundle
```

The eight `camayoc/<pack>/ns.ttl` files use the registered namespace IRIs.
Core ontology facts have their own `camayoc/core/ontology.ttl` file; they are
not folded into the SHACL entrypoint. Core observation debt remains a separate
companion file, preserving its
existing separation from rejecting constraints. The bundle also carries shared
declarations, identity links, explicit
`owl:equivalentProperty` bridges for properties and class-equivalence links.
Identity links alone do not rewrite predicate positions in Quipu. The manifest
records every input/output hash and declares `automatic_load: false`.

The builder replaces exact RDF IRIs, preserves literals and blank-node
structure, and checks that the inverse mapping recovers each original graph.
Executable query text refuses rather than retaining a query against the old
namespace. Changed mapping snapshots and existing output directories refuse.

The released Quipu parser reads `onViolation` before ontology reasoning.
Stable candidates therefore keep that legacy engine annotation alongside its
new IRI, so an emitting constraint cannot silently become rejecting.

The existing source-release producer also carries this generated bundle under
`namespaces/intigraph/bundle/`, with its manifest hash in `source.json`. It
compiles the selected immutable Git archive; dirty checkout bytes cannot enter
the release. This adds no release job or automatic installation.

The original ontology, shapes, profiles, rules and their paths remain intact.
These candidates do not load shapes, install aliases, change pins, rewrite
stored data, remove old identifiers or update an installer. Before replacing a
loaded set, prove old/new queries, positive and negative validation, actual
producer admission timing and the rollback on the intended consumer. Keep old
sets and files until that proof is accepted. Source equivalence is not runtime
acceptance.
