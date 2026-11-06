# Agent-memory w3id replacement proposals

Status: source candidate; no production schema or query loaded by this change.

The memory vocabulary extends the shared Intigraph ledger with
`namespaces/intigraph/agent-memory.json`. Frozen stage-one and stage-two
mappings retain their exact bytes. New memory terms use the Camayoc core pack;
existing terms keep their reviewed destinations, including `issuedBy` and
`Decision` in the Quipu namespace. Individual and natural-key IRIs stay unchanged.

`build_stable_namespaces.py` includes the extension in its canonical core
`ns.ttl` and separate memory vocabulary candidate. Core includes only the
approved scoped profiles, never the held Decision profile. It does not publish or load
anything. The proposal generator emits canonical declarations with `sameAs`,
`equivalentClass` and `equivalentProperty` bridges to the legacy terms.

Reactive materialization is off on the production store. Consequently the
profiles explicitly target both class spellings and both natural-key predicates,
and constrain each spelling through a separate literal-predicate property shape.
Required fields accept either spelling. The maximum is per predicate, not over
the alias union: a dual-spelling field can carry up to twice its former bound.
Cross-alias cardinality is an explicit remaining engine/producer-admission gate,
not a guarantee of these source profiles. Mixed records remain supported.
Class constraints accept either class IRI. This also validates mixed records;
two separate profiles would permit a mixed Directive to evade its scoped rule.
The vocabulary includes explicit type declarations for legacy aliases so existing
create tools remain governed without reactive restatement. These aliases do not
establish reader compatibility or authorize a writer flip.

The production validator was exercised read-only against the generated profiles:
legacy and canonical Preference pass; legacy Directives without natural keys
pass; memory Directives missing a rule fail in legacy, canonical and mixed form.
The scoped Decision proposal remains generated for review but excluded from the
requested load. No existing Directive is rewritten or promoted by this change.

Before acceptance, review the replacement bodies, file them as new proposals,
and supersede the old proposals. Persist exact reviewed profile bytes in IaC.
Acceptance still requires the registered memory feed, a real memory record,
first-page latency below the reader budget, and a verified mirror/recall result.
The feed reader must handle exact old/new IRIs before canonical records are used.
A successful source check is not production delivery.

The original proposal49/50 alternative paths plus maxCount caused every post-state
write to refuse in a snapshot rehearsal. Replacement source profiles use only
literal predicate paths for cardinality; they require an actual lab write test,
not just `/validate`. Existing pending proposal bodies are not changed or accepted
by source regeneration. Proposal48 and the held04 Decision profile stay unchanged.
