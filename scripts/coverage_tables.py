#!/usr/bin/env python3
"""The per-question coverage tables — one per competency slice.

Split out of `scripts/query_coverage.py` when the suite reached six slices:
the tool is logic, these are a judgment, and only a human can say that
`camayoc_blockers_by_evidence_kind` answers verification Q10. What is NOT a
judgment is whether a named file exists or whether the terms it uses exist —
`query_coverage.py` checks both, so a table cannot quietly claim coverage it
does not have.

THREE STATES, KEPT APART ON PURPOSE. `query_coverage.py`'s own docstring
names EXPRESSIBLE and STORED as different levels and then, until 2026-08-25,
reported both shortfalls as one word. They are not the same finding:

    {"query": "name"}                a stored query answers it        STORED
    {"expressible": "reason"}        the ontology carries the terms;
                                     nobody has written the query     UNWRITTEN
    {"gap": "...", "needs": [...]}   the ontology cannot express it    GAP

An UNWRITTEN question is work with a known shape. A GAP is a competency gap —
camayoc-b6h's finding, reported as itself, never answered from the nearest
term. Collapsing them makes a slice of unwritten queries look like a slice of
missing vocabulary, which is the more excusable of the two and the wrong
answer.

EVERY SLICE HAS A TABLE, AND THAT IS ENFORCED. `tests/test_coverage_slices.py`
asserts set equality between this file's slice names and `competency/*.md`,
and between each table's question numbers and the questions the suite parser
finds in that file. A new competency slice cannot land without a table, and a
new question cannot land without a row. That guard exists because this repo
found the same defect twice in this same tool — see that file's header.
"""

from __future__ import annotations

#: Question -> the stored query that answers it, or None with the reason.
#:
#: Hand-maintained because the mapping is a judgment: only a human can say that
#: `camayoc_blockers_by_evidence_kind` answers Q10. What is NOT hand-maintained
#: is whether the named file exists or whether the terms exist — both are
#: checked below, so this table cannot quietly claim coverage it does not have.
#: slice name -> {question number -> coverage entry}. report() takes the slice.
COVERAGE_VL: dict[int, dict] = {
    1: {"query": "camayoc_verification_falsifier"},
    2: {"query": "camayoc_verifications_without_falsifier"},
    3: {"query": "camayoc_falsifiers_not_rerun"},
    4: {"query": "camayoc_adversarially_proven_checks"},
    5: {"query": "camayoc_check_variable_dependence"},
    6: {
        "query": None,
        "gap": "No Principal class and no observed stop/heartbeat record, so 'is "
               "this principal running' has nothing to join against. Correctly "
               "NOT a stored fact — ingress rule 5 forbids storing judgments that "
               "decay — but the read-time join needs an observed liveness record "
               "to join TO, and none is modelled. aegis:Session now exists (§D, "
               "camayoc-e29) and carries its principal via aegis:actor, but a "
               "session is not a liveness record: nothing says the session is "
               "still running. This remains the largest gap and it blocks the "
               "four-beads-one-cause family the paper leads with.",
        "needs": ["Principal", "observed stop record (frm/item/item_status/ts)"],
    },
    7: {"query": "camayoc_blocked_on_closed_dependency"},
    8: {
        "query": None,
        "gap": "No Alert or Escalation class. Same missing liveness join as Q6, "
               "plus the subject class itself.",
        "needs": ["Alert", "Escalation", "Principal liveness record"],
    },
    9: {
        "query": None,
        "gap": "Only createdAt and closedAt are modelled, so elapsed time WITHOUT "
               "a state transition is not derivable — the intermediate transitions "
               "are not recorded as facts.",
        "needs": ["StateTransition", "transitionedAt"],
    },
    10: {"query": "camayoc_blockers_by_evidence_kind"},
    11: {"query": "camayoc_execution_path_for_mechanism"},
    12: {"query": "camayoc_drifted_execution_paths"},
    13: {"query": "camayoc_single_owner_execution_paths"},
    # Section D (cost and effort accounting) numbers itself 16-21 — there is
    # no Q14/Q15 in the suite file. These rows were absent from this table
    # until 2026-08-22, which silently understated the slice's denominator —
    # the exact defect incident-corpus.md §4.2 documents. A gap uncounted is
    # a gap unreported.
    # §D became expressible with camayoc-e29 (2026-08-22): Session +
    # UsageRecord + provider/tokensConsumed/inSession/attributedTo, with
    # observedAt and actor REUSED rather than re-minted.
    16: {"query": "camayoc_work_item_token_cost"},
    17: {"query": "camayoc_principal_consumption_by_provider"},
    18: {"query": "camayoc_work_per_token"},
    19: {"query": "camayoc_sessions_without_usage"},
    20: {"query": "camayoc_provider_burn_window"},
    21: {"query": "camayoc_decision_cost"},
}


COVERAGE_GP: dict[int, dict] = {
    1: {"query": "camayoc_gp_trajectory_replay"},
    2: {"query": "camayoc_gp_trajectories_for_topic"},
    3: {"query": "camayoc_gp_admissible_exemplars"},
    4: {"query": "camayoc_gp_unverified_stretch"},
    5: {
        "query": None,
        "gap": "Per-step provenance-cone membership is computed by `quipu path "
               "cone` (quipu-gp2, design-only) and only its OMISSION verdicts "
               "are modelled (PathOmission with authority cone-analysis). A "
               "term for stored in-cone/out-of-cone status per step is "
               "deferred until that command exists — its output shape decides, "
               "and minting the term first would be modelling a mechanism's "
               "internals before the mechanism.",
        "needs": ["per-step cone-membership facts (shape decided by quipu path cone)"],
    },
    6: {"query": "camayoc_gp_omissions"},
    7: {"query": "camayoc_gp_dead_ends"},
    8: {"query": "camayoc_gp_blessing_history"},
    9: {"query": "camayoc_gp_superseded_paths"},
    10: {"query": "camayoc_gp_promotion_queue"},
    11: {"query": "camayoc_gp_paths_for_similar_work"},
    12: {"query": "camayoc_gp_conformance"},
    13: {"query": "camayoc_gp_backtest_outcomes"},
    14: {
        "query": None,
        "gap": "aegis:derivedConstraint is minted, but no Policy or capability-"
               "grant records exist to join against — the audit chain "
               "constraint <- path <- exemplars needs the L5 mechanisms "
               "(gated on verdict signing). Deferred with the later ladder "
               "levels, per the suite's acceptance note.",
        "needs": ["aegis:Policy records reachable from this graph", "grant/act records"],
    },
    15: {
        "query": None,
        "gap": "No record of individual authorized ACTS exists, so 'which acts "
               "were authorized by path-derived constraints' has nothing to "
               "join against. Same L5 dependency as Q14.",
        "needs": ["act records carrying their authorizing constraint"],
    },
    16: {
        "query": None,
        "gap": "Staleness is a read-time judgment over conformance evidence "
               "accumulated by the guard (yupana FR-41, design-only). "
               "deviatesAt is minted; the guard that writes conformance "
               "records at scale does not exist yet.",
        "needs": ["guard-written conformance records over time"],
    },
}

#: metrics-and-requirements. The slice where the omission cost the most: four
#: stored queries have answered these questions since 2026-08-07 and counted
#: toward nothing, so the tool UNDERSTATED real coverage while looking
#: complete. The remaining gaps are two families — the liveness join this
#: repo already tracks, and the fact that a requirement's CHANGE is not
#: modelled (only its current state is).
COVERAGE_MR: dict[int, dict] = {
    1: {"query": "camayoc_metrics_for_subject"},
    2: {
        "query": None,
        "gap": "The ownership half is answerable today (aegis:ownedBy is "
               "mandatory on both Metric and NonFunctionalRequirement). The "
               "second half — 'is that owner currently able to act' — is the "
               "same missing liveness join as verification Q6/Q8, and the "
               "slice says so itself: alert keepers were found pointing at "
               "stopped principals seven times out of ten. Answering only the "
               "half that is modelled, and presenting it as the answer, is "
               "precisely the adjacency defect the incident corpus catalogues.",
        "needs": ["Principal", "observed stop/heartbeat record"],
    },
    3: {"expressible": "NonFunctionalRequirement carries about, ownedBy and "
                       "createdAt, all sh:minCount 1 — 'for what service, by "
                       "whom, when' is a three-property projection. Unwritten, "
                       "not blocked."},
    4: {"query": "camayoc_unvalidated_metric_claims"},
    5: {"expressible": "Unmeasured promises: NonFunctionalRequirement with "
                       "FILTER NOT EXISTS { ?m aegis:measuresRequirement ?r }. "
                       "Every term exists."},
    6: {"expressible": "Orphan instrumentation, the mirror of Q5: Metric with "
                       "no measuresRequirement. Note that the shape makes this "
                       "mandatory, so against conforming data the honest answer "
                       "is 'none' — and the query is still worth storing, "
                       "because it is the shape's own falsifier."},
    7: {"query": "camayoc_metric_retrieval_method"},
    8: {"query": "camayoc_metric_reachability_candidates"},
    9: {
        "query": None,
        "gap": "No edge from a Decision to the Metric it cites as evidence. "
               "aegis:rationale is prose and aegis:blockerEvidence/"
               "promotionEvidence are about other subjects entirely; matching "
               "a metric to a decision by name inside the rationale string is "
               "the 'matching the description, not the thing' form (A5) that "
               "the incident corpus records. The edge is a single mint, but "
               "it is a mint, and it waits its turn.",
        "needs": ["an edge from Decision to the Metric it cites as evidence"],
    },
    10: {
        "query": None,
        "gap": "Half is free: aegis:threshold is on the requirement and quipu "
               "gives the as-of replay, so 'what was the threshold on <date>' "
               "is answerable. 'Who had changed it' is not. ownedBy names the "
               "standing owner, not whoever re-asserted the value, and the "
               "writer is episode metadata in the store rather than a fact in "
               "the graph — so the audit question this slice exists for is the "
               "half that cannot be answered.",
        "needs": ["an attribution on a threshold re-assertion, distinct from "
                  "aegis:ownedBy"],
    },
    11: {
        "query": None,
        "gap": "'Relaxed' is a comparison between two versions of a threshold, "
               "and 'in service of what decision' is an edge from that change "
               "to a Decision. Neither the change event nor the edge is "
               "modelled — only the current value is. Same root as Q10: the "
               "requirement's history is bitemporal in the store but is not a "
               "subject in the graph, so nothing can be said ABOUT a change.",
        "needs": ["a requirement-change subject", "an edge from that change to "
                  "the Decision that motivated it"],
    },
    22: {"expressible": "A regex over the requirement's rdfs:label for the "
                        "unquantified words the slice lists (quickly, large, "
                        "sufficient, as needed, reasonable). Expressible, and "
                        "the slice credits Pennant for the better answer: catch "
                        "it in a deterministic analyzer at AUTHORING time, not "
                        "at measurement time. A stored query here is the "
                        "backstop, not the fix."},
    23: {"expressible": "threshold, unit and measurementWindow all exist and "
                        "are all sh:minCount 1 on the requirement shape, so "
                        "against conforming data every requirement passes. "
                        "Storing it is still right: it is how you find the "
                        "requirements that entered before the shape did."},
}

#: crew-task-lifecycle. Slice 1, and the one with no stored queries at all —
#: the decision vocabulary is the oldest thing in the ontology and the least
#: queried. Every gap here is one of the two designs this repo has
#: deliberately deferred: Principal/liveness, and StateTransition.
COVERAGE_CTL: dict[int, dict] = {
    1: {"expressible": "Decision carries about, chose and rationale. This is "
                       "the question the whole slice exists for and it has no "
                       "stored query — the plainest instance of 'an uncounted "
                       "question is a gap unreported'."},
    2: {"expressible": "aegis:over holds the alternatives visible at the time, "
                       "and its absence is itself meaningful (the term's own "
                       "comment says so), which a stored query must render as "
                       "'none articulated' rather than as no rows."},
    3: {"expressible": "aegis:decidedIn, whose comment already admits the "
                       "looseness a stored query has to settle: it points at "
                       "'the work item (or session)' a decision was made "
                       "under, so the query must say which it accepts."},
    4: {"expressible": "aegis:decidedBy joined to aegis:decidedIn."},
    5: {"expressible": "Contested pairs are a read-time judgment over stored "
                       "facts, which is exactly what ingress rule 5 asks for: "
                       "two Decisions sharing aegis:about, differing in "
                       "aegis:chose, neither carrying supersededBy. No term is "
                       "missing; the judgment must not be stored."},
    6: {
        "query": None,
        "gap": "No class for a standing convention. A Decision tagged "
               "declared is adjacent — it is a choice made at a moment, not a "
               "rule that stands until revoked — and answering 'what "
               "conventions apply here' from it would report decisions as "
               "conventions. quipu owns aegis:Policy and camayoc reuses before "
               "minting, so the open question is a reuse decision, not a mint. "
               "Related: camayoc-95a evaluates aegis:appliesTo for the "
               "path-scoped half of exactly this question.",
        "needs": ["a standing-convention subject, or a decision to reuse "
                  "quipu's aegis:Policy", "a scope term binding one to an area"],
    },
    7: {"expressible": "aegis:supersededBy, which the term's comment describes "
                       "as saying who won without deleting the loser."},
    8: {"expressible": "WorkItem joined on aegis:about. Note the adjacent "
                       "surface: scripts/ingest_git_provenance.py emits "
                       "aegis:implements/aegis:modifies edges that answer a "
                       "sharper version of this, and neither term is declared "
                       "in ontology/core.ttl — a separate finding, not this "
                       "question's blocker."},
    9: {
        "query": None,
        "gap": "createdAt, assignedTo, closedAt and outcome give the endpoints. "
               "'Worked' — the intermediate transitions, with timestamps — is "
               "not recorded as facts at all, so the LIFECYCLE cannot be "
               "returned, only its first and last frames. Identical to "
               "verification Q9, and deliberately deferred with it: "
               "ontology/core.ttl:68-70 records the decision not to pre-empt "
               "the StateTransition design.",
        "needs": ["StateTransition", "transitionedAt"],
    },
    10: {
        "query": None,
        "gap": "Answerable only as an approximation, which is worse than not "
               "answerable. Without transitions the best available join is "
               "'assigned to this principal, with a created/closed interval "
               "overlapping the window' — under which an item assigned in June "
               "and closed in August reads as worked on in July. A plausible "
               "wrong answer is the failure mode this whole repo is built "
               "around, so this is registered as a gap rather than stored with "
               "a caveat nobody will read.",
        "needs": ["StateTransition", "transitionedAt"],
    },
    11: {"expressible": "aegis:closedAt with aegis:outcome, whose closed "
                        "vocabulary the shape already enforces."},
    12: {
        "query": None,
        "gap": "The stop payload (frm/item/item_status/ts) is an observed "
               "record no producer writes into the graph. This is verification "
               "Q6's missing liveness record seen from the work side, and it "
               "is the recursive instance the incident corpus quotes: the bead "
               "documenting invisible in-progress work was itself sitting "
               "in-progress on a stopped agent.",
        "needs": ["Principal", "observed stop record (frm/item/item_status/ts)"],
    },
    13: {"expressible": "aegis:sourceKind, mandatory on every subject the "
                        "shapes gate — aspect 1 of the paper, unqueried."},
    14: {"expressible": "A named-graph query against the plane IRIs "
                        "scripts/planes.py registers: what exists in "
                        "crew:inferred and nowhere that outranks it. The whole "
                        "quarantine mechanism is built and this question, which "
                        "is what quarantine is FOR, has no stored query."},
    15: {"expressible": "Bitemporal replay is quipu's and comes free; the "
                        "question exists to pin that the ontology never blocks "
                        "it. A stored query here is the proof of that claim."},
}

#: document-structure-and-chunks. The split is clean and worth reading: the
#: SECTION and SYMBOL half runs against a live graph (three repos, ingested
#: hourly) and is merely unwritten; the CHUNK half has no emitter, and the
#: slice's own acceptance forbids minting chunk terms until one is measured
#: live. That is a gap held open on purpose, not an oversight.
COVERAGE_DSC: dict[int, dict] = {
    1: {
        "query": None,
        "gap": "Chunk membership needs chunk nodes. bobbin:Chunk and "
               "bobbin:inDocument exist in shapes/code-entities.ttl as "
               "ADVISORY shapes only, and no emitter writes them — the slice's "
               "acceptance holds the terms at advisory 'until the emitter that "
               "writes it is measured live'. Blocked in bobbin, deliberately.",
        "needs": ["chunk facts from a measured bobbin emitter"],
    },
    2: {
        "query": None,
        "gap": "Same missing emitter, plus ordering: bobbin:chunkOrder is "
               "advisory and unwritten, so 'in what order' has nothing to sort "
               "by and would silently return document order instead.",
        "needs": ["chunk facts from a measured bobbin emitter", "bobbin:chunkOrder"],
    },
    3: {
        "query": None,
        "gap": "Sections exist and are live; the chunk-to-section containment "
               "edge does not. The answer would have to be inferred from "
               "offsets, which is a model fact wearing a parser's clothes.",
        "needs": ["chunk facts from a measured bobbin emitter",
                  "a chunk-to-section containment edge"],
    },
    4: {
        "query": None,
        "gap": "bobbin:nextChunk is advisory and unwritten. Note the shape of "
               "the wrong answer: with no adjacency edge, 'what follows' would "
               "be answered by chunk id ordering, which is adjacent to "
               "adjacency and not it.",
        "needs": ["bobbin:nextChunk written by a measured emitter"],
    },
    5: {"expressible": "Section, bobbin:headingDepth, bobbin:inDocument and "
                       "bobbin:contains are all live in the code-entity graph. "
                       "Unwritten, not blocked."},
    6: {
        "query": None,
        "gap": "'First chunk of a section' needs both halves the chunk emitter "
               "owes: containment and order.",
        "needs": ["chunk facts from a measured bobbin emitter", "bobbin:chunkOrder"],
    },
    7: {"expressible": "bobbin:contains between Sections, live."},
    8: {"expressible": "bobbin:headingDepth on Sections of a Document, live."},
    9: {
        "query": None,
        "gap": "No term distinguishes a standalone block (table, fenced code) "
               "from prose. Answering from Section containment alone would "
               "return the whole section and call it the blocks.",
        "needs": ["a block subject with its kind (table | fenced code | ...)"],
    },
    10: {"expressible": "bobbin:definedIn with bobbin:symbolKind, live against "
                        "the code-entity graph."},
    11: {"expressible": "bobbin:references / bobbin:touches from Sections to "
                        "symbols, live."},
    12: {"expressible": "bobbin:repo and bobbin:filePath, live — the plainest "
                        "question in the slice and still unwritten."},
    13: {
        "query": None,
        "gap": "Needs a per-chunk content hash recorded with a time, and the "
               "chunk emitter writes neither. aegis:contentHash exists but is "
               "carried on Metric, for reconciliation, not on chunks.",
        "needs": ["chunk facts from a measured bobbin emitter",
                  "a per-chunk content hash with its observation time"],
    },
    14: {
        "query": None,
        "gap": "The plane machinery is built and the question is right; there "
               "are simply no chunk-level facts to attribute yet. This row "
               "should flip to unwritten the moment the emitter lands, and it "
               "is the row that proves the emitter tagged its output.",
        "needs": ["chunk facts from a measured bobbin emitter"],
    },
}

#: workflow-and-archive. The vocabulary and the move rule landed 2026-08-24;
#: the stored queries and the shuttle fixture landed 2026-08-25 (camayoc-rkb)
#: against tests/fixtures/workflow-archive.trig — a TriG DATASET, because
#: this slice's questions are about where facts live as much as what they
#: say, and a single-graph fixture would answer them all from one pile.
#:
#: What remains is not unwritten work. Every one of the seven gaps is the
#: SAME boundary and it is a real one: graph kinds, freeze state, thaw
#: records and composed dataset labels are properties of quipu's meta-graph
#: and its store tables, served by GET /graphs, not triples any SPARQL query
#: can reach. Q6 is the one exception and it is a different boundary — SPARQL
#: can fetch a public key and cannot evaluate a signature against it.
#:
#: The Q6/Q7 gaps used to name a second cause: this tool's own grounding
#: self-check read only minted terms, so a query correctly REUSING a
#: quipu-owned aegis term was reported UNGROUNDED. That was the tool
#: penalising the repo's reuse-before-minting rule, and it is fixed —
#: ontology/core.ttl records the reuse with rdfs:isDefinedBy and
#: query_coverage.ontology_terms() reads it. Q7 is stored as a result.
COVERAGE_WA: dict[int, dict] = {
    1: {"query": "camayoc_wf_runs_in_window"},
    2: {"query": "camayoc_wf_run_state_as_of"},
    3: {"query": "camayoc_wf_transition_history"},
    4: {"query": "camayoc_wf_window_closeout"},
    5: {"query": "camayoc_wf_declared_vs_traversed"},
    6: {
        "query": None,
        "gap": "The identity-graph half is no longer the blocker — the "
               "fixture carries VerifierRegistrations and "
               "camayoc_wf_unverifiable_transitions performs exactly this "
               "join for Q7. What is left is the verb: Q6 asks whether the "
               "signature VERIFIES, and SPARQL cannot evaluate an ed25519 "
               "signature over a canonical message. A query can return the "
               "agent, the signature and the registered public key and "
               "stop there. Reporting that as coverage would let 'a key is "
               "registered' pass for 'the signature is good', which is the "
               "same shape of wrong answer as counting an unmeasured "
               "session as zero cost.",
        "needs": ["the verification RESULT as a write-time fact — quipu's "
                  "write gate (quipu-8cc) checking the signature at ingress "
                  "and recording the outcome, which is a fact true at write "
                  "time and not a judgment that decays",
                  "or a verification step outside SPARQL, which makes this a "
                  "question no stored query answers alone"],
    },
    7: {"query": "camayoc_wf_unverifiable_transitions"},
    8: {"query": "camayoc_wf_agent_activity_across_windows"},
    9: {
        "query": None,
        "gap": "Graph kinds and freeze state live in quipu's meta-graph and "
               "are served by GET /graphs, not by SPARQL over this ontology. "
               "The question also carries its own capability probe — a 404 "
               "means the store predates graph kinds and must read as 'cannot "
               "tell', never as 'no graphs' — which a stored query has no way "
               "to express.",
        "needs": ["the graph-kind labels as queryable facts, or a non-SPARQL "
                  "coverage level for store-surface questions"],
    },
    10: {
        "query": None,
        "gap": "Whether a dataset's composed kind label includes archive is a "
               "property of the dataset, not of any triple in it. Same "
               "store-surface boundary as Q9. Note what the stored queries "
               "DO settle: camayoc_wf_agent_activity_across_windows names "
               "its archive graph in a FROM, so which windows an answer "
               "needed is visible in the query text even though the label "
               "is not queryable.",
        "needs": ["composed dataset kind labels as queryable facts"],
    },
    11: {
        "query": None,
        "gap": "quipu:lifecycleState, quipu:frozenInto (the pack's content "
               "hash) and quipu:frozenAt are meta-graph facts about a graph. "
               "Same boundary as Q9, and the authorisation half — who "
               "authorized the freeze — is the part camayoc most wants and "
               "least controls.",
        "needs": ["meta-graph freeze facts reachable from a query"],
    },
    12: {
        "query": None,
        "gap": "Half graph, half store, and only the graph half is now "
               "answerable: camayoc_wf_window_closeout reports every run in "
               "a window as completed or open, which settles 'is every run "
               "terminal'. 'Operational kind and not yet frozen' is a "
               "meta-graph label, so the two halves still cannot be joined "
               "into the freezability verdict the question asks for. Half an "
               "answer is not coverage.",
        "needs": ["meta-graph kind and lifecycle labels joinable against run "
                  "state (the run-state half is camayoc_wf_window_closeout)"],
    },
    13: {"query": "camayoc_wf_plane_promotion_record"},
    14: {
        "query": None,
        "gap": "A thaw keeps the frozen_packs row with thawed_at rather than "
               "deleting it — a store table, not graph facts. Same boundary "
               "as Q9/Q11. The question is right and camayoc is the wrong "
               "place to answer it from.",
        "needs": ["thaw records reachable from a query"],
    },
}

COVERAGE_PI: dict[int, dict] = {
    1: {"query": None, "expressible": "The scoped activeBlocks closure and declared priority identify active P0 dependents; a dedicated blocker listing is not yet stored."},
    2: {"query": "camayoc_effective_work_priority"},
    3: {"query": None, "expressible": "Rule truth maintenance withdraws entailments after closure or edge removal; a before/after verification query is not yet stored."},
    4: {"query": None, "expressible": "Explicit project and companion datasets bound the query scope; isolation and unrelated-edge negatives are fixture controls, not a stored audit query."},
    5: {"query": None, "expressible": "The rule requires schema:Action typing and an active status; a stored rejected-entity audit query is not yet written."},
    6: {"query": None, "gap": "The direct Quipu rank-change and owner-notification workflow integration is not implemented.", "needs": ["direct workflow rank-change and notification records"]},
    7: {"query": None, "gap": "The P0 acceptance-disposition policy catalog and workflow integration are not yet modeled.", "needs": ["P0 acceptance disposition catalog"]},
}

#: delivery-and-dora (aegis-rb3): CDEvents deployment outcomes in window graphs.
COVERAGE_DD: dict[int, dict] = {
    # Every question needs aegis:DeploymentEvent, proposed as Quipu schema
    # proposal 31 (pending adjudication). Until it is accepted and lands in the
    # loaded vocabulary, nothing here is expressible; camayoc_deployments_by_day
    # is written and waits on it.
    1: {"query": None, "gap": "camayoc_deployments_by_day is written but its class is not yet in the ontology.", "needs": ["aegis:DeploymentEvent (Quipu proposal 31)"]},
    2: {"query": None, "gap": "Needs the DeploymentEvent class; then the latest succeeded/rolledback event per environment answers it.", "needs": ["aegis:DeploymentEvent (Quipu proposal 31)"]},
    3: {"query": None, "gap": "Needs the DeploymentEvent class (attemptedArtifactId, artifactId).", "needs": ["aegis:DeploymentEvent (Quipu proposal 31)"]},
    4: {"query": None, "gap": "Needs the DeploymentEvent class; then a weekly count per deployedService.", "needs": ["aegis:DeploymentEvent (Quipu proposal 31)"]},
    5: {"query": None, "gap": "Needs the DeploymentEvent class; the closed outcome set makes CFR a count.", "needs": ["aegis:DeploymentEvent (Quipu proposal 31)"]},
    6: {"query": None, "gap": "No merge record is joined to an artifact yet: lead time needs change.merged (or work-item close) events carrying the same artifact or commit.", "needs": ["aegis:DeploymentEvent (Quipu proposal 31)", "CDEvents change.merged mapping and records"]},
    7: {"query": None, "gap": "Needs the DeploymentEvent class; then the next succeeded event after a rolledback/failed one or an Incident.", "needs": ["aegis:DeploymentEvent (Quipu proposal 31)"]},
    8: {"query": None, "expressible": "Declared Tool records in catalog/delivery-emitters.json carry emitterTool, emitterStandard, emitterHow, emitterStatus and emitterDocs. The README renderer queries those exact catalog identities in a disposable local graph; this is source support, not runtime producer coverage."},
    9: {"query": None, "gap": "ReleaseEvent mapping is source-supported; live schema, query and producer admission are pending.", "needs": ["aegis:ReleaseEvent", "publication producer coverage"]},
    10: {"query": None, "expressible": "eventActor on ChangeEvent names the acting merge principal when supplied; absent on older records means unknown."},
}

#: github-activity (aegis-c01c.3): GitHub entities and append-only activities.
COVERAGE_GH: dict[int, dict] = {
    # Every question needs the GitHub classes (shapes/github.shapes.ttl), which
    # go through Quipu's governed schema path before a store accepts them.
    # Until then nothing here is expressible against the loaded vocabulary.
    1: {"query": None, "gap": "camayoc_github_pr_current_state is written (and answers on a test store) but its classes are not yet in the ontology.", "needs": ["aegis:GitHubActivity (Quipu schema proposal)"]},
    2: {"query": None, "gap": "Needs GitHubReview and GitHubActivity (reviewState, commitSha) in the ontology.", "needs": ["aegis:GitHubActivity (Quipu schema proposal)"]},
    3: {"query": None, "gap": "Needs GitHubActivity and GitHubWorkflowRun in the ontology; then a join of PR head, run conclusion and reviews.", "needs": ["aegis:GitHubActivity (Quipu schema proposal)"]},
    4: {"query": None, "gap": "camayoc_github_default_branch_ci is written (and answers on a test store) but its classes are not yet in the ontology.", "needs": ["aegis:GitHubActivity (Quipu schema proposal)"]},
    5: {"query": None, "gap": "Needs GitHubRelease, GitHubPush and GitHubActivity in the ontology.", "needs": ["aegis:GitHubActivity (Quipu schema proposal)"]},
    6: {"query": None, "gap": "camayoc_github_participating_threads is written (and answers on a test store) but its classes are not yet in the ontology; participation is judged at read time, never stored.", "needs": ["aegis:GitHubActivity (Quipu schema proposal)"]},
}

COVERAGE_WB: dict[int, dict] = {
    1: {"query": "camayoc_work_item_burndown"},
    2: {"query": "camayoc_work_item_burndown"},
    3: {"query": "camayoc_work_item_burndown"},
    4: {"query": None, "expressible": "The aggregate and canonical Seeds cohort can be compared, but timing and independent tracker-count verification are external controls, not an authored stored acceptance query."},
}

SLICES: dict[str, dict[int, dict]] = {
    "work-item-burndown": COVERAGE_WB,
    "verification-and-liveness": COVERAGE_VL,
    "golden-paths": COVERAGE_GP,
    "metrics-and-requirements": COVERAGE_MR,
    "crew-task-lifecycle": COVERAGE_CTL,
    "document-structure-and-chunks": COVERAGE_DSC,
    "workflow-and-archive": COVERAGE_WA,
    "priority-inheritance": COVERAGE_PI,
    "delivery-and-dora": COVERAGE_DD,
    "github-activity": COVERAGE_GH,
}
