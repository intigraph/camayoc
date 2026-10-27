# Design: The Bootstrap Ontology — the upper vocabulary every domain imports

> **Implementation status (2026-08-22):** ✅ **Built, the way this document
> asked to be built — slice by slice, question first.** `ontology/core.ttl` +
> `shapes/core.shapes.ttl` now carry the candidate set and three slices
> beyond it: `WorkItem` and `Decision` landed as classes, `Outcome` landed as
> the `aegis:outcome` property (what happened is a fact of the work item, not
> a node), and `sourceKind` is minted and SHACL-mandatory exactly as §2
> specified. One deliberate departure from §1's letter, recorded in
> `ontology/core.ttl`'s namespace note: terms are declared in the store's
> configured base namespace (`aegis:` by default) so that quipu `/episode`
> writes and the declarations agree out of the box — the namespace is still a
> parameter, set in that file's prefix, never a hardcoded hostname. Every
> term remains owed to a question in [competency/](https://github.com/intigraph/camayoc/tree/main/competency), now
> six suites (workflow-and-archive joined on 2026-08-24; `just competency-list`
> is the living count and watermark); per-question coverage is measured by
> `just query-coverage` rather than asserted here.

## 1. Principle: reuse before minting

The stack already has vocabulary with settled semantics. The bootstrap
ontology's first job is to **not duplicate it**:

| Existing | Namespace | Reused for |
|---|---|---|
| `aegis:` (quipu shapes) | governance atoms: `Policy`, `Principal`, `authorityOver`, `reports_to`, `appliesTo`, verdicts | agents, authority, governance |
| `prov:` (W3C PROV-O) | `wasGeneratedBy`, activities | episode provenance (quipu supplies this automatically on episode writes) |
| `quipu:` control vocabulary | labels (`freshness`, `trust`, `policyClass`), datasets, packs | trust posture, planes |
| `bobbin:` code entities | `CodeModule`, `CodeSymbol`, `Document`, `Section` | task ↔ code linkage (later slice) |

`aegis:appliesTo` is listed there as a **standing reuse constraint rather than
a current dependency** — camayoc uses it nowhere today. quipu declares it for
repo-relative path globs scoping where an action-boundary policy binds. The
2026-08-25 evaluation (`camayoc-95a`) read the whole suite against it and
found no question that needs path scoping: two questions carry an `<area>`
parameter (`crew-task-lifecycle` 6, `verification-and-liveness` 2) and neither
defines `<area>` as a path, so reading one as a path glob would be inventing
the meaning rather than reusing the term. The row exists so that if a camayoc
rule ever does scope by path, it reuses this term instead of minting the
equivalent — which is the failure the table is here to prevent.

Camayoc mints terms only where no owner exists. Those live under a
**parameterized namespace** — `camayoc:` resolving to a deployment-configured
IRI base, never a hardcoded hostname (the lesson shantytown's quipu client
already records: a namespace is a parameter, and a literal hostname in a
public repo is two faults with one cause).

## 2. The upper terms (candidate set — the slice decides)

Small and brutally curated. Current candidates, each owed to a competency
question in [competency/](https://github.com/intigraph/camayoc/tree/main/competency):

- **`camayoc:WorkItem`** — a unit of intended work (a shantytown task, a bead,
  an issue). Deliberately tracker-agnostic: shantytown's adapter stance is
  "bring your own tracker," so the ontology models the *work*, not the
  tracker's message format. (Modeling the message instead of the domain is
  the classic failure; the tracker record is evidence, never the model.)
- **`camayoc:Decision`** — a choice made in the course of work: what was
  decided, the alternatives visible at the time, who/what decided, and under
  which work item. The load-bearing class of the first slice.
- **`camayoc:Outcome`** — what actually happened: done, abandoned, superseded,
  failed-with-diagnosis. Distinct from the decision that aimed at it.
- **`camayoc:sourceKind`** — the ingress tier tag, mandatory on every
  camayoc-ingested fact: `observed` (deterministic parse of a record that
  exists), `declared` (a human said so), `inferred` (a model concluded it).
  See [ingress.md](ingress.md) — this is the generalization of
  NeuralAmplifier's `ruleTier`, and SHACL refuses facts without it.
- **Roles, not subclasses.** A work item *plays* roles (clinical/financial in
  the claims framing; here: a task is simultaneously a unit of dispatch, a
  provenance anchor, and a decision context). Facets over deep hierarchies —
  the semilattice stance, applied to the ontology itself.

Agents are **not** re-modeled: an agent is an `aegis:Principal` with
shantytown's `reports_to` shape, exactly as the graph already holds them.

## 3. Vocabulary discipline (all inherited, all mandatory)

- **`rdfs:range` only — never `rdfs:domain`** on shared predicates. A domain
  declaration on a predicate used across classes silently retypes whatever it
  touches once a reasoner materializes it (quipu's recorded Q-SARC-VOCAB
  lesson).
- **SHACL posture: permissive on domain shape, strict on provenance.** Closed
  `sh:in` vocabularies with `minCount 1` on `sourceKind` and its companions
  from the first load — the tag is the reader's only signal of trust. Domain
  properties start permissive and tighten deliberately.
- **Facts true at write time; judgments at read time.** Shantytown's event
  store learned this the hard way: a liveness verdict stamped at emit is
  stale by the time it is read. The ontology never contains a class or
  property whose value is a judgment that decays — currency, liveness, and
  status-now are queries, not stored fields.
- **Contested is first-class.** Two sources disagreeing produce a contested
  pair, queryable as such; resolution is an explicit, provenance-carrying act.

## 4. Packaging

The bootstrap vocabulary ships as **`core.pendant.db`** (quipu #81): the upper
terms, their SHACL, and the generic competency queries (e.g. "what decisions
touch entity X"). Domain packs (`crew.pendant.db` first) declare it in their
manifest's default dataset. Until packs land in quipu, the same content lives
here as Turtle + shape files loadable via `POST /shapes` — the pack is the
distribution format, not a prerequisite.

## 5. Related

- [ingress.md](ingress.md) — how facts carrying this vocabulary enter quipu.
- [task-lifecycle-slice.md](task-lifecycle-slice.md) — the slice that mints
  the first real terms.
- quipu `docs/design/graph-labels.md` — the trust/freshness lattice this
  vocabulary's planes are labelled with.
- quipu `docs/design/knowledge-packs.md` — the artifact format.
- NeuralAmplifier `docs/knowledge-architecture.md` — the proven instance of
  the tier-tagged, SHACL-refused ingress posture this generalizes.
