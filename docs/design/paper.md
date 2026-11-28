# Design: Camayoc paper plan — how knowledge earns its way into the graph

> **Implementation status (2026-08-22, second revision):** 🟡 **Planned.**
> Nothing drafted. The evaluation is no longer thin: **16 of the verification
> slice's 19** competency questions run as stored queries
> (`just query-coverage`), after `camayoc-89e` landed the five single-edge
> mints (Q3/Q4/Q5/Q7/Q12) and `camayoc-e29` the §D cost-accounting vocabulary
> (Q16–21), both on 2026-08-22. The figure's history matters for §5: it was
> reported as 5/13 until earlier the same day, when the coverage tool was
> found to be omitting §D from its denominator (5/19 once counted). The
> three gaps that remain — Q6, Q8, Q9 — are all the Principal/liveness
> modelling, which is a design decision, not an edge. A newer slice, golden
> paths, stands at 12/16 stored. Read
> [`implemented-set.md`](implemented-set.md) before drafting a single sentence;
> it is the gate on what this paper may claim.

## Status

- **Date:** 2026-08-22 (rev 2; rev 1 2026-08-17)
- **Status:** Planning. Boundary agreed; implemented set measured; evaluation gap narrowed to the liveness family (Q6/Q8/Q9) and quantified.
- **Related:** [`implemented-set.md`](implemented-set.md) (what may be claimed),
  [`thesis-boundary.md`](thesis-boundary.md) (the camayoc/yupana split),
  [`incident-corpus.md`](incident-corpus.md) (the empirical core),
  [`ingress.md`](ingress.md) (the mechanism),
  [`../vision.md`](../vision.md) (the TrustGraph foil),
  the provisional filing (kept privately) (D — **claims more than is built**).

## 1. Intent and thesis

**Thesis, from the README:** *how knowledge earns its way into the graph, so
that the store never has to trust an extractor.*

Sharpened for a paper:

> A knowledge store that accepts machine-written facts has a trust problem it
> cannot solve internally, because by the time a fact is in the store the
> question "why should I believe this?" has already been answered — badly.
> **The answer must be a refusal at admission, not a confidence score at read
> time.** And the refusal must itself be falsifiable, or it is decoration.

Two halves, and the second is what makes it a paper rather than a design note.
Anyone can add a mandatory provenance tag. The contribution is applying the
same standard *to the checks themselves*: a `Verification` is refused at ingress
unless it names the result that would have proved it wrong.

That constraint sounds pedantic until you have field data. We have field data
(§4), and it says that on one night on a live crew, **eleven separate checks
could not have failed** and were reported as verification by careful people.

## 2. Positioning

### The three-paper program

Worded identically here and in yupana's plan:

- **quipu** compiles SARC into the **store** — submitted to arXiv (no public identifier yet).
- **yupana** compiles it into the **action**.
- **camayoc** governs **admission** — what may enter at all.

The nearest external anchor is **SARC-DQ**, which quipu's related-work section
describes as gating evidence quality. Evidence quality is precisely camayoc's
territory rather than yupana's, and that is the one-sentence justification for
the admission leg being its own paper.

### The named foil: TrustGraph

Already stated in [`../vision.md`](../vision.md) and it should open the paper,
because it is concrete and it is a real system rather than a straw one:

> The temptation is to bolt an extraction pipeline onto the store. TrustGraph
> and its peers do exactly that, and it works — until you ask "why should I
> believe this fact?" and the answer is "a model wrote it during ingestion."

The paper's structural claim follows: **the extraction layer must live outside
the store, with its own discipline, its own tests, and its own refusals.** Be
fair to the foil — the integrated approach has real ergonomic advantages, and
the paper is stronger for saying so before arguing the trade is wrong for
agent-written knowledge specifically.

### The boundary with yupana

Settled in [`thesis-boundary.md`](thesis-boundary.md) and mirrored in yupana's
plan §2.1. Short version: camayoc owns **admission**, yupana owns **the guard's
non-answers at the action boundary**, neither claims the shared principle, each
cites the other. Two collisions were found and resolved there — the installer
gate proof (camayoc's, yupana cites) and `Empty|Partial|Full` coverage
reporting (convergent, both keep, neither claims).

## 3. Contributions

- **C1 — Provenance-refusing ingress with a closed, per-class-narrowed
  vocabulary.** `aegis:sourceKind` is mandatory and constrained to
  `observed | declared | inferred`; a write carrying an unrecognised value fails
  now, not at read time. Built; refusal tested
  (`tests/test_knot_provenance.py`).
- **C2 — Falsifier-gated verification.** `aegis:falsifier`, `sh:minCount 1`, on
  every `Verification`. A verification that does not name what would have
  disproved it is refused at ingress rather than stored and believed. Built.
  **This is the paper's sharpest single mechanism** and the one with no obvious
  precedent in knowledge-graph ingress.
- **C3 — The incident corpus.** Eleven measured instances of unfalsifiable
  verification and a four-mechanism/one-missing-predicate liveness family, from
  one night on a live crew, taxonomized into seven recurring forms. See §4 and
  [`incident-corpus.md`](incident-corpus.md). **Most papers about verification
  integrity have no field data at all.**
- **C4 — Installation-time gate proof.** Each refusal gate is proven by a probe
  that omits exactly one required property, so the arms discriminate — the
  paper's own thesis applied to the paper's own installer. Built
  (`scripts/gate_probe.sh`, `tests/test_gate_probe.py`), and **independence is
  now established** (`camayoc-104`): each probe provably omits exactly one
  required property, and a store enforcing one shape at a time proves only its
  own arm. Claimable as stated.
- **C5 — Liveness by deliberate absence, as a design position.** Judgments that
  decay are never stored; liveness is a read-time join. Claim the position and
  the vocabulary. **Do not claim the mechanism** — see §6.

## 4. The empirical core

Full write-up in [`incident-corpus.md`](incident-corpus.md); source is
`competency/verification-and-liveness.md`, recorded contemporaneously on
2026-08-06/07.

**Section A — verification integrity.** Seven forms, each measured at least
once:

1. a guard documented as belt-and-braces that was the *only* mechanism
2. a byte-identity test that passed against a read-write open
3. a schema check comparing `(type, name)` only, so `ADD COLUMN` was invisible
4. a binding condition whose two branches were the same function
5. a `grep -c` matching the *description* of the bug in the test payload
6. a `grep` against an empty capture whose `0` read as "absent"
7. a "corroboration" equally consistent with both competing hypotheses

The unifying shape — and the sentence the paper should be built around — is
that none of these was a wrong answer. Each was **a check performed against
something adjacent to the claim, and reported as verification.**

**Section B — held-work liveness.** Four simultaneous P1s, one root cause:
every routing mechanism recorded an owner without checking the owner was alive.
Bead assignment, alert keeper labels, `BLOCKED` status, `in_progress` status —
four mechanisms, one missing predicate, identical worst-case consequence: *a
thing correctly detected and correctly recorded is indistinguishable, from every
dashboard, from a thing that was never detected at all.* The sharpest instance
is recursive and should be quoted verbatim: the bead *documenting* invisible
in-progress work, sitting in-progress, invisible, on a stopped agent.

**Section C — armed ≠ merged ≠ correct.** A guard correct in git and absent
from every execution path for 24h; a rules file six weeks stale reaching agents
while the current one sat in the repo.

### The two attacks this section must survive

Named in `camayoc-101` and both are fair:

- **Selection bias.** Were these found by looking for them? What is the
  denominator? A corpus assembled by searching for instances of a pattern
  proves the pattern is findable, not that it is common.
- **Count vs rate.** "Eleven instances" is unanchored. Eleven out of what, over
  what period, on how many agents?

[`incident-corpus.md`](incident-corpus.md) answers both explicitly, including
where the honest answer is "we cannot compute this from the record we kept".
**Do not paper over that.** A corpus with a stated denominator gap is credible;
a corpus with a silent one is not, and the paper's own thesis is that a claim
must name what would falsify it.

## 5. Evaluation — and its honest state

**Methodology: competency questions, which are the standard for ontology work.**
As of 2026-11-28, the suite is 124 questions across eleven slices,
machine-readable and watermarked (`sha256:f752e8ed721a8917`,
`just competency-list`). Suite-wide coverage is 44 stored, 33 expressible and
unwritten, and 47 competency gaps (`just query-coverage`); the library holds
67 stored query files. The suite sets its own bar:
*every question must eventually run as a named stored query.*

**Coverage of the verification-and-liveness slice: 16 of 19 (`Partial`).**
Reported per question by `scripts/query_coverage.py` (`just query-coverage`),
and the figure is pinned by a test so it cannot drift silently.

The figure's history is itself evaluation material. `camayoc-102` measured
5/13; the 2026-08-22 audit found the coverage tool omitting the slice's own
§D (cost accounting, Q16–21) from its denominator — 5/19 once counted, an
uncounted question being a gap unreported. Later the same day `camayoc-89e`
landed the five single-edge mints (Q3/Q4/Q5/Q7/Q12) and `camayoc-e29` the §D
vocabulary (Q16–21), each term owed to its question: 16/19.

> **A suite-wide denominator now exists, and this section does not yet use
> it (2026-08-25).** The same defect recurred one level up: `SLICES` in the
> coverage tool listed two of the suite's six files, so four slices and 56
> questions carried no verdict — and four already-stored metrics queries
> counted toward nothing, meaning the tool understated real coverage while
> appearing complete. All six slices now have tables, a test asserts set
> equality between them and `competency/*.md` so an uncounted slice cannot
> land again, and `just query-coverage` prints a suite total: **40 of 91
> stored, 22 expressible and unwritten, 29 competency gaps** (32/91 when the
> denominator first existed; `camayoc-rkb` took workflow-and-archive from
> 0/14 to 8/14 later the same day). The per-slice figures above are unchanged
> and remain correct.
>
> **What number this section should lead with is an authorial decision and is
> deliberately left open.** 16/19 is the strongest true statement available
> and 40/91 is the most complete one; they are not in conflict, and choosing
> between them — or reporting both, with the three states kept apart, since
> an unwritten query and a competency gap are different claims about the
> ontology — is a framing call for whoever drafts §5, not a figure to be
> swapped in by a sweep.

| | Questions | State |
|---|---|---|
| Stored and executing | Q1–Q5, Q7, Q10–Q13, Q16–Q21 | falsifier retrieval and staleness, adversarial proof, variable dependence, closed-dependency blocks, blockers by evidence kind, execution paths and drift, cost accounting |
| **Competency gaps** | Q6, Q8, Q9 | all the Principal/liveness modelling, each reported with the terms it would need |

The remaining gaps are one family, and it is the pointed one:

- **Q6, Q8 and Q9 (liveness) block the family §4 leads with.** There is no
  `Principal` and no observed stop/heartbeat record. This is *correctly* not a
  stored fact — ingress rule 5 forbids storing judgments that decay — but a
  read-time join still needs something to join *to*, and nothing is modelled
  (`aegis:Session`, minted for §D, carries a principal but says nothing about
  running). **The paper must say this plainly: the four-beads-one-cause
  corpus motivates a capability the ontology cannot yet express.**
- **Q4 is now a closed loop worth a paragraph.** `camayoc-104` established
  proven-able-to-fail for the gate probes; `aegis:adversariallyProvenBy` now
  records the same property about any check, and the proof node is itself a
  falsifier-gated `Verification`. The system practises the discipline *and*
  can describe it — and the date each half landed is in the history.
- **Q2 has a subtlety worth a footnote.** A `Verification` without a falsifier
  cannot be written through a gated ingress, so Q2's population is precisely the
  store's *pre-gate legacy*, and against a store that was always gated it
  correctly returns nothing. The fixture seeds pre-gate rows so the query is
  proven able to find them.
- **§D's acceptance is self-application again.** Q16 and Q18 run against a
  fixture whose totals are stated independently (what the harness files
  print) and reproduced by the queries — a usage reader checkable only
  against itself being the section-A defect in an accountant's hat. The
  UNKNOWN-never-zero rule is tested from both sides: unmeasured items and
  decisions return no row, and Q19 returns the unmeasured session itself.

Every stored query executes against a fixture with **both arms** — a seeded
positive finding and a control negative — per the suite's own acceptance
criterion: *a query that cannot return findings has the same defect as the
checks in section A, one level up.* That self-application is the sentence to
put in §7.

**What this means for the claim.** The liveness corpus in §4 still
**motivates** a capability rather than demonstrating one, and the paper must
say so — but the shortfall is now three questions, one named family. The
verification-integrity and cost-accounting halves of the slice are
demonstrable, not merely motivated.

## 6. Scope boundaries (honest)

From [`implemented-set.md`](implemented-set.md), which is authoritative:

- **Quarantined inference and governed promotion are BUILT; one quipu-side
  mechanism is not.** This bullet said the opposite until 2026-09-05, and was
  contradicting the very document it cites as authoritative — see the note
  below, which is kept because how it went stale is the reusable part.
  Re-measured 2026-09-05:
  - *Quarantined inference* — built on both sides. Camayoc routes inferred
    facts to a distinct named graph labelled low in the trust lattice
    (`scripts/planes.py`, 13 tests) since 2026-08-18; quipu materialises into
    companion inferred graphs with a write guard (`src/store/inferred.rs`,
    17,732 bytes, 13 functions) since 2026-08-27, `df36f72`.
  - *Governed promotion* — camayoc's half is built (`scripts/promote_plane.py`,
    15 tests, authority-gated and self-promotion-refused, move half since
    2026-08-24). **Quipu's promotion mechanism is not**: `grep -ci promote
    src/store/inferred.rs` -> `0`, and quipu's own `entailment-regime.md` still
    lists *"REMAINING under quipu-0b6: the promotion mechanism (§3 —
    authority-gated…)"*.

  So future work names **one** blocker, not two, and it is quipu-side rather
  than camayoc-side.

  > **How this went stale, recorded because the mechanism is the point.** The
  > claim was true when written (2026-08-22). `implemented-set.md` re-measured
  > on 2026-08-25 and moved both aspects to Built; quipu's half landed
  > 2026-08-27. This bullet moved on neither date, and for fourteen days
  > asserted "NOT built" while naming as authoritative a document that said
  > "Built" — a contradiction inside one repo, which no cross-repo watch would
  > have caught. `tests/test_paper_claims.py` now fails on exactly that shape.
  > It also carried a falsifiable claim, *"Zero non-doc references exist"*,
  > which was false by two scripts and 28 tests.
- **Tier-honest serving is yupana's.** Camayoc defines and mandates the tag;
  yupana serves under it. Claim the vocabulary, not the serving.
- **Typed non-answers: one instance, not a taxonomy.** `NO COVERAGE` on the
  ontology's own coverage. The systematic taxonomy is yupana's.
- **Liveness: a position and a vocabulary, not a mechanism.** The terms exist
  (`ontology/core.ttl:93, :120`) and the refusal to synthesise a write-time
  liveness predicate is explicit and deliberate
  (`shapes/core.shapes.ttl:140`). The read-time queries do not exist.

## 7. Paper outline

1. **Introduction** — the TrustGraph question: why should I believe this fact?
2. **Background** — SARC and SARC-DQ; the three-paper program; what quipu and
   yupana already claim.
3. **The admission problem** — why a store cannot solve trust internally.
4. **Mechanism** — closed provenance vocabulary, write-time refusal, the
   falsifier constraint on `Verification`.
5. **Proving the gates** — installation-time probes, one omitted property each,
   arms that discriminate. Self-application of the thesis.
6. **Field evidence** — the incident corpus, with denominator and rate stated.
7. **Evaluation** — competency questions, per-question coverage, gaps as
   findings.
8. **Limitations** — §6, in full and unhedged.
9. **Related work** — TrustGraph and extraction-in-store platforms; SARC-DQ;
   provenance vocabularies (PROV-O — say plainly why `sourceKind` is narrower
   and why narrowness is the point); SHACL validation as a gate rather than a
   report; ontology competency questions (Grüninger & Fox) as evaluation
   methodology.
10. **Conclusion.**

## 8. Build order

1. ~~Agree the yupana boundary~~ — done, [`thesis-boundary.md`](thesis-boundary.md).
2. ~~Produce the implemented set~~ — done, [`implemented-set.md`](implemented-set.md).
3. ~~Write up the incident corpus with denominator and rate~~ — done, [`incident-corpus.md`](incident-corpus.md).
4. ~~Prove the gate probes adversarially~~ — done (`camayoc-104`); C4 is claimable.
5. ~~Close the competency-query gap for the verification slice~~ — done
   (`camayoc-102`), reaching 5/13 at the time and converting the other 8 into
   named ontology gaps. (Recounted as 5/19 on 2026-08-22 when the slice's §D
   entered the denominator — six more named gaps, no lost queries. The five
   single-edge mints landed as `camayoc-89e` and the §D cost vocabulary as
   `camayoc-e29`, both 2026-08-22: 16/19.)
6. **Decide whether to close the liveness gap before drafting.** Q6/Q8/Q9
   (the Principal/liveness modelling) is all that remains, and it is the one
   that changes what §4 can claim; it can also ship as future work with the
   blocker named. This is now the only open build decision, and it is ian's.
7. Draft §§1–5 from the mechanism docs; they are stable.
8. Draft §7 last, against whatever coverage step 6 settles on.

## 9. What this paper is not

- Not a retrieval paper. That is bobbin's, and its own measurement problems are
  documented there.
- Not a store paper. Quipu's is submitted and its evaluation is spent.
- Not a guard paper. Yupana owns the action boundary and the non-answer
  taxonomy.
- Not a claim that quipu-side *promotion* of quarantined facts is built — it is
  not (see §6). Quarantined inference itself is built and claimable; this line
  said otherwise until 2026-09-05.
