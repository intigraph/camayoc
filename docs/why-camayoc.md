# Why camayoc

The long form that used to open the README: its place in the stack, what this
repository owns, what runs today, what it deliberately is not, and the name.
The README now leads with a three-command first success
([the caboodle-stack README standard](https://github.com/scbrown/caboodle/blob/main/docs/stack/README-STANDARD.md)).

## Role in the stack

```text
shantytown / git / yupana / sessions        (activity: raw fiber)
        │
        ▼
    camayoc                                  (ontology + ingress + packs)
        │  episodes, SHACL-tagged, tiered
        ▼
     quipu                                   (governed bitemporal store)
        │
        ▼
    bobbin / agents                          (retrieval, context, RAG)
```

- **[quipu](https://github.com/scbrown/quipu)** stores and governs; it
  deliberately contains no extraction and no LLM.
- **[Yupana](https://github.com/scbrown/yupana)** observes code structure. The
  `hank` executable name remains only as a compatibility alias.
- **[bobbin](https://github.com/scbrown/bobbin)** serves knowledge back into
  agent context.
- **[shantytown](https://github.com/scbrown/shantytown)** runs the crew whose
  activity is the first knowledge domain.
- **camayoc** is the layer TrustGraph-style platforms put *inside* the store
  and this stack deliberately keeps outside it: ontology, ingress discipline,
  and distribution.

## What this repo owns

1. **The bootstrap ontology** — the small, brutally curated upper vocabulary
   every domain imports: work, agents, decisions, outcomes, source tiers.
   Competency questions before classes, always.
   [docs/design/bootstrap-ontology.md](design/bootstrap-ontology.md)
2. **The ingress discipline** — how facts enter quipu: episode-shaped,
   SHACL-refused without provenance tags, deterministic-first, LLM-inferred
   knowledge quarantined into low-trust planes.
   [docs/design/ingress.md](design/ingress.md)
   The declarative structured-source lane is specified as a governed RML/R2RML
   subset in [docs/design/rml-executor.md](design/rml-executor.md).
3. **Domain ontologies as certified knowledge packs** — each domain ships as
   Quipu's `.pendant.db` (quipu #81): graph + shapes + competency queries +
   labels + manifest, one attachable file. A Camayoc pack is the
   `CertifiedShareBundle`, not a second format; publisher and certifier claims
   bind independently to the manifest content hash.
   [docs/design/certified-knowledge-packs.md](design/certified-knowledge-packs.md)
4. **The competency-question suites** — the questions agents actually ask,
   maintained as the test harness for every ontology change. Six slices now:
   task lifecycle, metrics, verification-and-liveness (with its §D cost
   accounting), golden paths, document structure and chunks, and
   workflow-and-archive. [competency/](https://github.com/intigraph/camayoc/blob/main/competency/)

### Metrics and nonfunctional requirements

Camayoc catalogues metric definitions and executable retrieval methods; it does
not copy time-series samples into Quipu. Prometheus rule ingress is a reconciled
producer snapshot, so changing or removing a rule converges the catalogue
without appending duplicate episode comments:

```bash
scripts/reconcile_metrics.sh /path/to/prometheus/rules https://prometheus.example
```

The first argument may be one rule file or a directory containing YAML/Jinja
rule templates. The optional endpoint is stored as a retrieval parameter; when
it is absent, retrieval reports `unreachable` instead of inventing a default.
The parser requires Python and PyYAML. `scripts/retrieve_metric.py` executes a
method returned by the `camayoc_metric_retrieval_method` named query and prints
`retrieved`, `unreachable`, `query_error`, or `unsupported`; it never persists
the returned sample. Authenticated Prometheus deployments are read from
`PROMETHEUS_BASIC_AUTH_USER` and `PROMETHEUS_BASIC_AUTH_PASSWORD`; credentials
are never stored in the graph method.

### Typed decisions with Jev

Some ingress questions are semantic judgments, not parser facts: *which
competency question is this asked question an instance of, if any?* *Does this new decision
collide with a settled one?* Camayoc's first answer to those was deliberately
lexical, and every verdict said so (`method: lexical-jaccard-v1`,
`semantic: false`). Since 2026-09-21 there is a second arm:
[Jev](https://typesafe.ai), TypeSafe's "System One" model, which returns typed,
calibrated decisions (`noul` yes/no probabilities, `choice` over up to 255
options, `score` over ordered levels) instead of text, in one parallel pass.

`scripts/jev.py` is the only place camayoc calls it. What that unlocked:

- **Question mapping as one typed decision.** `competency.py --method jev`
  poses a single `choice` per asked question over the whole competency suite
  plus a reserved `none-of-these` option, and reads Jev's probability per
  question. Paraphrases the word-overlap scorer could not resolve now map,
  with a confidence attached. (Whether a competency question is *covered* by
  a stored query is the separate, deterministic check in `query_coverage.py`;
  Jev never answers that.)
- **An abstention Jev does not have.** Jev cannot say "nothing fits"; the
  reserved option is how a forced choice becomes an honest **NO COVERAGE**.
  When it wins, coverage is `Empty` whatever the runner-up scored.
- **The same honesty rules as before.** The verdict carries
  `method: jev-latest-choice-v1`, `semantic: true`, the instructions posed, the
  model that answered, the confidence and the none probability. No key means a
  loud `JevUnavailable`, never a quiet fall back to word overlap under a Jev
  label.
- **Ingress discipline unchanged.** Every Jev answer is a model judgment. If it
  is written back it is `inferred` and lands in the quarantine plane; Jev never
  promotes, and plane routing stays deterministic.

```bash
export TYPESAFE_API_KEY=...   # from your secret store
python3 scripts/competency.py --method jev "which metrics can we retrieve for the database host right now?"
python3 scripts/competency.py --method lexical "which metrics can we retrieve for the database host right now?"
python3 scripts/jev.py noul --state "..." --ask "Does this message request a refund?" --dry-run
```

Design, candidate slots and the caveats (no rationale, forced choice, context
rot): [docs/design/jev-typed-decisions.md](design/jev-typed-decisions.md).
The settled-decision collision check (`noul`) is the next arm.

## What runs today

The ingress discipline stopped being a table in a design doc. The pieces below
are implemented, and — the part this repo cares most about — their *refusals*
are tested, not just their acceptances.
[docs/design/implemented-set.md](design/implemented-set.md) keeps the
measured, claim-by-claim ledger.

- **Quarantine planes** (`scripts/planes.py`) — writes route by `sourceKind`
  into named graphs labelled in quipu's trust lattice: `inferred` never shares
  a plane with `observed` and always ranks strictly below it. Registration and
  labelling happen together or not at all; an unknown `sourceKind` refuses
  instead of defaulting to ROOT; and against a quipu without the
  `/graph/create` + `/graph/label` routes, bootstrap **fails** rather than
  quietly writing everything into ROOT.
- **Two-dimensional routing** — `plane_for(source_kind, data_kind)`.
  `knowledge` goes to the static planes; `operational` data (workflow runs,
  shuttle's export) goes to time-windowed graphs (`scripts/windows.py`,
  `{WINDOW_NS}{family}/{YYYY-MM}`) so a completed window can be deep-frozen
  whole. Unknown pairs refuse; nothing defaults to ROOT.
- **Plane promotion** (`scripts/promote_plane.py`) — how a fact earns its way
  out of quarantine. Authority-gated and failing closed (a missing or
  unreadable grant file means *nobody* may promote, not everybody),
  self-promotion refused independently of authority, upward moves only, and
  the move rule in full: assert in the target, **close** the source episode (a
  bitemporal close, never a delete), record the move — with
  `camayoc:sourceLeftOpen true` said out loud when the source stays open.
- **The workflow slice** — `WorkflowDefinition`, `WorkflowStep`,
  `WorkflowRun`, and append-only `TransitionEvent`s; `currentState` is
  re-asserted per transition, never mutated. Every term is owed to a question
  in [competency/workflow-and-archive.md](https://github.com/intigraph/camayoc/blob/main/competency/workflow-and-archive.md).
  [docs/design/workflow-and-archive.md](design/workflow-and-archive.md)
- **Golden paths** — verified trajectories, blessed and enforced:
  `Trajectory`, `GoldenPath`, `PathOmission`, `PathPromotion`, with SHACL
  refusals where absence would make the node a lie (a `GoldenPath` without
  its exemplar trajectory is refused) and a gate-probe arm proving each
  refusal. [docs/design/golden-paths.md](design/golden-paths.md)
- **Stored queries, honest coverage** — every named query in
  [queries/](https://github.com/intigraph/camayoc/blob/main/queries/) is tested against a seeded fixture with a positive
  *and* a control-negative arm. `just query-coverage` is the living figure:
  **40 of 91 stored across six slices**, plus 22 questions that are
  expressible with today's vocabulary and simply unwritten, and 29 that are
  competency gaps. Those last two are reported apart on purpose — an unwritten
  query is work with a known shape, an ontology gap is a finding — and a
  question with no stored query is never answered from the nearest term.
- **Cost accounting** — `Session` and `UsageRecord`, with six §D queries:
  token cost per work item, per-provider burn windows, sessions with no usage
  records, what a decision cost. No quota term exists on purpose — the
  consumption is ours to record; the ceiling is the provider's.
- **Refused-write denominator** (`just refusal-rate`) — joins quipu's durable
  `write.refused` event stream to the accepted `Verification` population and
  reports the refusal share the incident corpus never had. It reports and
  never writes: a refusal *rate* is a ratio between two moving populations, so
  it is a judgment computed at read time, not a fact true at write time. The
  share is a FLOOR three times over and the report says so on every run —
  `speculate` refusals are excluded from the stream, refused fact bodies are
  not stored (so no per-form breakdown is recoverable, ever, and `reason`
  names the gate rather than the failing shape), and a prospective stream
  divided by a retrospective population has a denominator that is too large.
  An unreachable store, or one predating the stream, exits 3: could not look
  is not zero.
- **git → work-item provenance** (`just ingest-git`) — walks commit history
  and emits the `WorkItem ←implements— GitCommit —modifies→ CodeModule` chain
  as Turtle. Deterministic, byte-identical on re-run, and pure `observed`. It
  abstains unless you declare the tracker prefix (`--project`), because no
  pattern separates a work-item id from ordinary hyphenated English, and a
  false match silently widens an item's scope.
- **Competency assessment** (`just competency "<question>"`) — scores a
  question against the suite and returns `Empty | Partial | Full`, with
  **NO COVERAGE** as a first-class verdict. Every verdict carries its method,
  thresholds, and suite watermark; the embedding scorer is wired and selects
  itself only when weights are actually present, so a verdict can never claim
  `semantic: true` over a word-overlap number.
- **Quarantined entity extraction** (`scripts/extract_entities.py`) — finds
  entity mentions in markdown prose deterministically: a gazetteer built from
  the graph's own labels plus explicit user-declared regex patterns, no model
  in the loop. Deterministic does not mean `observed` — a mention is a reading
  of the text, so every fact lands in the inferred plane, tagged, never ROOT.
  Ambiguous labels match for nobody, re-runs are byte-identical, and an
  unreachable store or unprovisioned plane refuses loudly before anything is
  written.
- **Settled-decision collision check** (`scripts/settled_decisions.py`) —
  scores a proposed decision against the standing human decisions and
  surfaces likely re-litigation *before* the write. Advisory, lexical and
  says so; its own recorded verdict routes to the inferred plane, because a
  machine's opinion about a human's decision must not sit beside it looking
  like one.
- **Advisory chunk shapes** — vocabulary for bobbin's chunk graph
  (`bobbin:Chunk`, `nextChunk`, `chunkOrder`) in `shapes/code-entities.ttl`,
  value constraints only until the emitter ships and is measured.
- **Certified-pack producer boundary** (`scripts/certify_pack.py`) — invokes
  Quipu's `.pendant.db` pack and verify path, reads the manifest content hash, and
  renders distinct publisher/certifier claims plus the governed source mapping.
  It verifies two distinct Ed25519 keys, derives the hash from a conforming
  machine-readable SHACL report, and scans the complete pack artifact before
  asserting scrub success. It atomically publishes a content-addressed durable
  copy and binds that file URI into the source mapping. Static and frozen-window
  packs are exercised through relocation with disposable independent keys.
  Quipu's v1 share directory is the git/import projection of this same governed
  bundle: its `share_id` tracks lineage and never replaces the pack's canonical
  graph hash.
- **Governed RML executor** (`scripts/rml_executor.py`) — compiles standard
  RML/R2RML triples-map data, refuses unsupported constructs before source
  access, reads bounded JSON/CSV/SQLite sources, emits sorted duplicate-free
  N-Quads, and commits through Quipu's SHACL-governed named-graph write lane.

## What this repo is deliberately NOT

- **Not a store.** Quipu is the store. Camayoc never holds truth; it prepares
  and certifies it.
- **Not a harness.** Shantytown runs agents. Camayoc only defines what their
  activity *means*.
- **Not retrieval.** Bobbin serves context. Camayoc ships the queries, not the
  serving.
- **Not an extraction platform.** Deterministic parsers first; where an LLM
  infers, the output is labelled as inference and can never masquerade as
  observation. The tag is the reader's only signal of trust — that posture is
  inherited from NeuralAmplifier's datalinks pipeline, the proven instance of
  this pattern.

## On the name

The name honors the *khipukamayuq* (hispanicized *quipucamayoc*): the Quechua
title for the specialists of the Inca state charged with making, maintaining,
archiving, and interpreting the khipus — and answerable, personally, for what
the knots were read to claim. *Kamayuq* on its own means "specialist, keeper,
one who is charged with": Quechua formed many such titles (*punku kamayuq*,
doorkeeper; *chaka kamayuq*, bridge keeper). Beside a sibling project named
[quipu](https://github.com/scbrown/quipu), this repo's name completes the
compound.

This project borrows the role as a metaphor for a software layer that decides
what a fact may claim before it enters a knowledge graph. It does not claim to
represent Andean culture. Khipu is a living tradition — Quechua is spoken by
millions, community cord-keeping survived into the modern era in Andean
villages, and khipu scholarship is active — and the modern Quechua spellings
are *khipu* and *khipukamayuq*; this stack uses the older hispanicized forms
for continuity with its sibling repos.

The logo's centerpiece is the **yupana**, the Andean counting board drawn at
the khipukamayuq's side in Guaman Poma de Ayala's 1615 illustration of the
*contador mayor* — the only primary-source depiction of one: five rows of
cells holding five, three, two and one counters. It was the surface where a
value was worked out before being committed to the knots — which is exactly
this repo's job, so the keeper's own instrument sits at the center, between
the loose thread coming in and the knotted record going out.

## Status

Working ingress, honestly bounded. Of the eight aspects in the provisional
disclosure, four are built, four are partial, and none are design-only —
[docs/design/implemented-set.md](design/implemented-set.md) is the
measured ledger, re-run per row rather than carried forward on trust. The
competency suite spans six slices; 40 stored queries answer it where the
vocabulary exists, and `just query-coverage` reports the remaining gaps as
gaps. The quipu substrate this builds on is itself still in flight.
