# The implemented set — what the paper may claim

> **Implementation status (2026-08-25):** ✅ **Current — re-measured.** Every
> row below was measured against this repo on this date, with the grep or file
> that produced it. Re-run before drafting; do not carry a row forward on
> trust. This re-measure changed one verdict: **aspect 3 is no longer partial**
> — the retraction half of the move landed with the workflow slice on
> 2026-08-24 (`camayoc-913`) and the row had been carried forward on trust
> against the row two lines above it, which already said so. The 2026-08-22
> re-measure had changed two: aspects 2 and 3 were design-only on 2026-08-17
> and are no longer (`camayoc-s0h` and `camayoc-mip` closed 2026-08-18 with
> `scripts/planes.py` and `scripts/promote_plane.py`).

`camayoc-100` exists because a provisional patent and a systems paper have
different standards of proof. **A patent may claim constructively reduced
embodiments. A systems paper may claim only what is built and measured.**
Provisional D discloses eight aspects. This document states, per aspect, what
is built, what is partial, and what is design-only — so drafting starts from
the implemented set rather than from D's table of contents.

Getting this wrong is the fastest route to an unrecoverable review: a reviewer
who finds one unbuilt claim stops believing the built ones.

## Verdict table

| # | D aspect | State | Evidence |
|---|----------|-------|----------|
| 1 | Provenance-refusing ingress | ✅ **Built** | `shapes/core.shapes.ttl` makes `aegis:sourceKind` mandatory; `shapes/code-entities.ttl:50` constrains it to the closed `observed\|declared\|inferred` vocabulary. Refusal proven, not asserted — `tests/test_knot_provenance.py` (216 lines). |
| 2 | Quarantined inference | ✅ **Built** (camayoc-s0h, 2026-08-18) | `scripts/planes.py` registers the planes as quipu named graphs and labels them in the trust lattice — both or neither, and `plane_for` refuses an unknown `sourceKind` rather than defaulting to ROOT. Refusals pinned by `tests/test_planes.py` (13 tests): `inferred` never shares a plane with `observed` and always ranks strictly below it. Runtime caveat to state: it requires quipu's `POST /graph/create` + `/graph/label` routes (added upstream for the bead); against an older quipu it reports UNAVAILABLE and bootstrap *fails* rather than writing to ROOT. See `docs/design/ingress.md` §2.1. |
| 2b | Windowed operational ingress + the move rule | ✅ **Built** (2026-08-24) | Routing is 2-D: `plane_for(source_kind, data_kind)` refuses `operational` toward the static planes; `scripts/windows.py` creates-and-labels month windows (`operational`/`fresh`/`soleRecord`) for deep freeze; the same change landed the promotion move rule, which is now carried by aspect 3's row rather than duplicated here. Pinned by `tests/test_planes_2d.py` (16 tests). See `docs/design/workflow-and-archive.md`. |
| 3 | Governed promotion | ✅ **Built** (camayoc-mip 2026-08-18; the move half, camayoc-913, 2026-08-24) | `scripts/promote_plane.py`: authority-gated (fails closed on a missing `config/plane-authority.json`), refuses self-promotion, refuses non-upward moves, refuses promoting what is not in the source plane; records `camayoc:planePromotion` with full provenance. 15 tests in `tests/test_promote_plane.py`, almost all refusals, plus the happy-path control. It is now a graph **move**, not a copy-up: `promote()` returns `(assert, close)` and `main` commits assert → `POST /episode/retract` on `--source-episode` (`on_orphan: preserve`) → record. The retraction is a **bitemporal interval close, never a delete**, which is what answered the governance question that held this row open: valid-time replay still shows the fact where and when it lived (competency/workflow-and-archive.md Q13). Commit order is load-bearing and tested both ways — a failed close leaves the fact readable in two planes at different trust (exit 4, stated out loud with the exact retry body), where close-then-failed-assert would lose it outright. Absent `--source-episode` the record carries `camayoc:sourceLeftOpen true` rather than implying a close that never happened. Pinned by `tests/test_planes_2d.py` `MoveRuleTests` + `MoveRuleCommitOrderTests` (8 tests). |
| 4 | Falsifier-gated verification | ✅ **Built** | `shapes/core.shapes.ttl:89-95` — `aegis:falsifier`, `sh:minCount 1` on `Verification`, with the refusal message. Probed by `scripts/gate_probe.sh`; the probe's own discrimination is `camayoc-104`. |
| 5 | Liveness by deliberate absence | 🟡 **Partial — and the partiality is the point** | The *vocabulary* exists (`ontology/core.ttl:34, :120` — `aegis:ExecutionPath`, `aegis:ownedBy`; the 2026-08-17 row cited two term names that do not appear in the file), and the deliberate absence is explicit at `shapes/core.shapes.ttl:130`: liveness "belongs with yupana/quipu, not a made-up write-time liveness rule". What does **not** exist is the read-time side: the liveness-join questions (verification-and-liveness #6, #8, #9) have **no stored queries** — #10 gained one (`camayoc_blockers_by_evidence_kind`) with `camayoc-102`, and #7 gained `camayoc_blocked_on_closed_dependency` with `camayoc-89e` (2026-08-22; not a liveness join — it needs only `blockedOn` and the already-modelled `closedAt`). See §2. |
| 6 | Installation-time gate proof | ✅ **Built, and now independence-proven** | `scripts/gate_probe.sh` with `tests/test_gate_probe.py` holding the probes to being able to fail. `camayoc-104` closed the remaining gap: each probe is shown to omit exactly one required property (parsed from the shapes, not hardcoded), and a store enforcing one shape at a time proves only its own arm. Claimable as stated. |
| 7 | Tier-honest serving | 🟡 **Partial** | `aegis:tier` is carried on policies (`shapes/policies/edit-grounding.ttl:49, :71, :78`) and the tier vocabulary is in `ontology/core.ttl:54`. But camayoc does not *serve* facts — yupana does. What camayoc owns is the tag's definition and its mandatory-ness, not the serving. **Claim the vocabulary; do not claim the serving.** |
| 8 | Typed non-answers | 🟡 **Partial, one instance** | Fully realised in exactly one place: `scripts/competency.py` returns `Empty \| Partial \| Full` with a `NO COVERAGE` verdict, method/threshold/watermark carried, 18 tests. That is a real instance of the discipline. It is **one** instance, on the ontology's own coverage, not a general non-answer taxonomy across camayoc's surfaces. |

**Summary: five built (1, 2, 3, 4, 6), three partial (5, 7, 8), none
design-only.** (On 2026-08-17 this read three/three/two; aspects 2 and 3
landed on 2026-08-18, and aspect 3's remaining half — the retraction — on
2026-08-24, which is what moved the count to five/three.)

## 1. What this means for the paper

**Claim without qualification (aspects 1, 4, 6).** Provenance-refusing ingress,
falsifier-gated verification, and installation-time gate proof are built, tested,
and — critically — their *refusals* are tested, not just their acceptances. These
three are the paper's spine and they are strong: the falsifier shape in
particular is a genuinely unusual constraint to enforce at write time, and it is
enforced.

**Claim narrowly, with the boundary stated (aspects 5, 7, 8).**

- *Liveness by deliberate absence* should be claimed as a **design position**
  with a vocabulary, not as a mechanism. The position is defensible and
  interesting — refusing to synthesise a write-time liveness predicate, because
  liveness is a read-time join against a store that actually knows — and
  `shapes/core.shapes.ttl:140` shows the refusal was deliberate rather than
  an omission. But the paper must not imply the liveness questions are
  answerable today. They are not; see §2.
- *Tier-honest serving* is yupana's, not camayoc's. Camayoc defines and mandates
  the tag; yupana serves under it. Claim the former. This is also the cleanest
  illustration of the boundary in `docs/design/paper.md` §2.
- *Typed non-answers* should be claimed as **one worked instance**
  (`NO COVERAGE` on ontology coverage) that demonstrates the discipline, with
  the general taxonomy attributed to yupana's guard, which has ten distinctions
  and the incidents that forced each.

**Now claimable, carefully (aspects 2, 3).** On 2026-08-17 this section said
*do not claim, at all* — quarantined inference and governed promotion were D's
headline pairing and were not built, both blocked on quipu routes. On
2026-08-18 both landed (`camayoc-s0h`, `camayoc-mip`; the quipu routes were
added upstream on the same branch). The claims the paper may now make, with
their edges stated:

- *Quarantined inference* is claimable as built: inferred facts route to a
  named graph labelled low in the trust lattice, registration and labelling
  are atomic, and an unroutable write fails rather than landing in ROOT. The
  caveat to state is deployment-shaped, not mechanism-shaped: it requires a
  quipu with the `/graph/create` + `/graph/label` routes, and the failure
  against an older store is a refusal, which is the honest direction.

> **Cross-repo blocker, as of 2026-09-05 (muldoon).** These two rows depend on
> quipu, and a row whose truth lives in a sibling repo goes stale without anyone
> here touching anything — this banner's trigger is *drafting*, which never
> fires on a dependency. So the blocker carries its OWN date, separate from the
> re-measure date above:
>
> | quipu-side mechanism | as of 2026-09-05 | evidence |
> |---|---|---|
> | quarantined materialisation | **BUILT** (landed 2026-08-27, `df36f72`) | `src/store/inferred.rs`, 17,732 bytes, 13 functions; companion inferred graphs + write guard |
> | governed promotion | **NOT built** | `grep -ci promote src/store/inferred.rs` -> `0`; quipu's `entailment-regime.md` still lists it under REMAINING |
>
> The first half cleared **two days after** the 2026-08-25 re-measure above, so
> that re-measure was correct when taken and wrong eleven days later. That is
> the failure mode this table exists to make visible, not a lapse.

- *Governed promotion* is claimable as **built, both halves**: the governance
  half — authority-gated, self-promotion-refused, upward-only,
  provenance-recorded, with fifteen tests that are almost all refusals — and,
  since 2026-08-24, the **move** half. `camayoc-913` was open because
  retracting from `crew:inferred` looked like erasing the low-trust past;
  bitemporality answers it, and the close is an interval close on the source
  *episode* rather than a delete, so as-of replay still finds the fact in the
  plane it was written to. The edge the paper must still state is the honest
  one the mechanism states itself: when the caller cannot name the source
  episode, the promotion proceeds with `camayoc:sourceLeftOpen true` on the
  record — a copy-up that says so, not a move that pretends. Saying
  "promotion" while meaning "copy-up with an audit record" *without* that
  flag would be exactly the adjacency defect the corpus documents; the flag
  is what keeps the claim clean.

## 2. The gap the audit surfaced: liveness is unqueryable

Aspect 5 turned up something the bead did not anticipate and that
`camayoc-102` needs.

*As this audit was written* the competency suite was 47 questions across three
files (`sha256:7a0cc4abad386885`) and the repository contained **four** stored
queries, all in `queries/`, all four serving the *metrics* slice. The figures
in this paragraph and the block quotes under it are kept in past tense on
purpose: they are the record of what was measured when, and the update chain
below is what supersedes them. The current figures are `just competency-list`
and `just query-coverage`, never this page.

```console
$ ls queries/
camayoc_metric_reachability_candidates.json
camayoc_metric_retrieval_method.json
camayoc_metrics_for_subject.json
camayoc_unvalidated_metric_claims.json
```

So the **verification-and-liveness slice had zero stored queries** — including
the four-beads-one-cause family (#6-#10) that `camayoc-101` proposes as the
paper's empirical core, and the falsifier questions (#1-#5) that motivate the
aspect the paper is strongest on.

That was a coverage figure of **4/47 ≈ 9%** when this audit was written.

> **Update after `camayoc-102`:** the verification-and-liveness slice now has
> **5 of its 13** questions stored and executing (`just query-coverage`), and
> working it produced a better finding than the count. **Only five of the
> thirteen can be expressed at all** with today's vocabulary; the remaining
> eight name terms the ontology does not carry, so they are *competency gaps*
> rather than unwritten queries. Aspect 5's read-time gap above is the largest
> of them: Q6 and Q8 need a `Principal` and an observed liveness record to join
> against, and neither is modelled.
>
> **Update 2026-08-22:** the suite is now five files (golden paths and
> document-structure/chunks joined it) and `queries/` holds **21** stored
> queries. `just query-coverage` is the living figure and reports two slices:
> verification-and-liveness **5/19** — the denominator grew from 13 because
> the slice's own §D (cost accounting, Q16–21) had been left out of the
> coverage table, and an uncounted question is a gap unreported — and
> golden-paths **12/16**, whose four gaps are all blocked outside this repo
> (quipu `path cone`; L5 verdict signing; yupana's guard). The eight original
> verification gaps are unchanged: nothing minted since covers them, and the
> five that are single-edge mints are tracked as `camayoc-89e`; the §D six as
> `camayoc-e29`.
>
> **Update 2026-08-22, later the same day:** both beads landed. `camayoc-89e`
> minted the five single edges (`verifiedAt`/`verifies`,
> `adversariallyProvenBy`, `dependsOnVariable`, `blockedOn`,
> `artifactDigest`/`sourceDigest`) and `camayoc-e29` the §D cost vocabulary
> (`Session`, `UsageRecord`, `provider`, `tokensConsumed`, `inSession`,
> `attributedTo`, with `observedAt` and `actor` reused). `queries/` now holds
> **32** stored queries and verification-and-liveness stands at **16/19**,
> pinned by test. The three that remain — Q6, Q8, Q9 — are all the
> Principal/liveness modelling: `Session` gives them a principal-bearing
> node but no observed stop/heartbeat record, so aspect 5's read-time gap
> above is now the WHOLE remaining gap of the slice.
>
> **Update 2026-08-25 — the same defect, one level up, and a structural fix.**
> The 2026-08-22 entry above congratulates itself on catching an undercounted
> denominator inside a slice. The tool was undercounting SLICES at the same
> time: `SLICES` listed two of the suite's six files, so `crew-task-lifecycle`
> (15), `document-structure-and-chunks` (14), `metrics-and-requirements` (13)
> and `workflow-and-archive` (14) — **56 questions** — carried no verdict at
> all. It also *understated* real coverage, which is the detail worth keeping:
> four stored queries shipped for the metrics slice on 2026-08-07 and counted
> toward nothing, so the tool reported less than the repo had while appearing
> complete.
>
> Restating the lesson is what failed, twice. The fix is a test, not a note:
> `tests/test_coverage_slices.py` asserts set equality between the coverage
> tables and `competency/*.md`, and between each table's question numbers and
> the questions the suite parser finds, so a slice or a question cannot land
> without a row. The report also now separates **UNWRITTEN** (the ontology
> carries the terms; nobody wrote the query) from **GAP** (the ontology cannot
> express it) — levels the tool's own docstring had always distinguished and
> its output had always collapsed.
>
> The honest suite-wide figure, first computable on this date: **32 of 91
> stored across six slices, 29 expressible and unwritten, 30 competency
> gaps.** Per slice: verification-and-liveness 16/19, golden-paths 12/16,
> metrics-and-requirements 4/13, and 0/15, 0/14, 0/14 for the three that had
> never been counted. Nothing regressed — 32 was always the number of stored
> queries; what changed is that there is now a denominator to put it over.
>
> **Update 2026-08-25, later the same day — `camayoc-rkb`: 32 → 40 of 91.**
> The workflow-and-archive slice went from **0/14 to 8/14**. Q1–Q5, Q7, Q8 and
> Q13 are stored queries in `queries/camayoc_wf_*.json`, executing against
> `tests/fixtures/workflow-archive.trig` — a TriG **dataset**, not a graph,
> because this slice's questions are about where facts live as much as what
> they say. 36 tests in `tests/test_workflow_queries.py`, each with a seeded
> positive and a control negative, and the controls are the interesting half:
> the same query composed against the wrong graphs returns a quietly short
> answer rather than an error, which is the failure the window scheme exists
> to prevent.
>
> **One gap closed by a tool fix, none by minting.** Q7 was recorded as
> blocked partly because `query_coverage.py`'s grounding self-check read only
> MINTED terms, so a stored query correctly reusing a quipu-owned aegis term
> (`signature`, `VerifierRegistration`, `verifier`) was reported UNGROUNDED —
> the tool penalising this repo's own reuse-before-minting rule. The fix is a
> reuse block in `ontology/core.ttl` recording each term's owner with
> `rdfs:isDefinedBy` and no minting declaration, which the check now reads.
> Nothing was minted in the aegis namespace to close a gap.
>
> **What stayed a gap, on purpose.** Six of fourteen. Q9–Q12 and Q14 are one
> boundary: graph kinds, freeze state, thaw records and composed dataset
> labels are quipu meta-graph and store-table properties served by
> `GET /graphs`, not triples SPARQL can reach — camayoc is the wrong place to
> answer them from and says so. Q6 is a different one: it asks whether a
> signature VERIFIES, and SPARQL can fetch a public key but cannot evaluate
> an ed25519 signature against it. Reporting the retrieval as coverage would
> let "a key is registered" pass for "the signature is good". The
> Principal / Session-stop / StateTransition family was not touched at all;
> `ontology/core.ttl`'s decision not to pre-empt that design stands.
>
> Suite-wide: **40 of 91 stored across six slices, 22 expressible and
> unwritten, 29 competency gaps.** Per slice: verification-and-liveness 16/19,
> golden-paths 12/16, workflow-and-archive 8/14, metrics-and-requirements
> 4/13, crew-task-lifecycle 0/15, document-structure-and-chunks 0/14.

The consequence for drafting order is narrowed and concrete: `camayoc-101`
writes up the incident night as a labelled corpus, and the natural reviewer
question is "could your system have *answered* these questions at the time?".
For section A (verification integrity) and §D (cost) the answer is now yes,
against fixtures with both arms; for section B (held-work liveness) it is
still no — a measured and attributable no, three questions blocked on the
Principal/liveness modelling. The paper states that the liveness corpus
motivates a capability rather than demonstrating one.

## 3. Method

Each row was produced by grepping for the mechanism outside `docs/` and
`competency/` — a term that appears only in design prose is design-only by
definition, which is exactly how aspects 2 and 3 were classified on 2026-08-17
— and how their reclassification was verified on 2026-08-22 (`scripts/planes.py`
and `scripts/promote_plane.py` exist outside `docs/`, with their tests). Gates
green at the time of writing: `just test` 46 passed (1 skipped), `just check` 0
issues across 17 files. After `camayoc-102` and `camayoc-104`: 65 passed, 21
files. At the 2026-08-22 re-measure: 145 ran, 0 failed (32 skipped — the
server-backed arms skip without a `quipu-server` binary), 24 files. At the
2026-08-25 re-measure: 210 ran, 0 failed (52 skipped, same reason), 26 files.
