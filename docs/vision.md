# Vision — governed memory for working agents

> **Status (2026-08-06):** founding document.

## The problem

Agent crews accumulate experience and throw it away. Decisions get made,
conventions get settled, failures get diagnosed — and the next session starts
cold, or worse, starts from a summary an LLM hallucinated about what happened.
The stack has a governed bitemporal store (quipu) precisely so knowledge can
persist with provenance; what it lacks is a disciplined way for *working
activity* to become *governed knowledge*.

The temptation is to bolt an extraction pipeline onto the store. TrustGraph
and its peers do exactly that, and it works — until you ask "why should I
believe this fact?" and the answer is "a model wrote it during ingestion."
Quipu's whole posture is that the store never trusts an extractor. So the
extraction layer must live outside, with its own discipline, its own tests,
and its own repo. This one.

## The bet

Three claims, each already proven once in this stack:

1. **Deterministic-first ingress produces facts worth governing.**
   NeuralAmplifier's datalinks pipeline parses game rules with deliberately no
   model in the loop, tags every fact with engine/tier/source under SHACL
   refusal, and the result is a knowledge plane agents can cite without
   caveats. Camayoc generalizes that pattern beyond one game.
2. **The graph is the truth; everything else is a projection.** Shantytown
   already derives agent hierarchy from `reports_to` edges by query — "the
   hierarchy is a query, not a thing to store." Extending that from identity
   to *memory* is the same move: store facts true at write time; derive
   judgments at read time.
3. **Knowledge should ship as artifacts.** Quipu's knowledge packs (#79–#82)
   make a domain — graph, shapes, competency queries, labels, manifest — one
   attachable, hash-verified file. Camayoc is the *producer* of those packs.

## The first domain: the crew itself

Agentic coding with shantytown is the right first domain because the loop
closes entirely within the stack: shantytown emits the work, yupana observes the
code, camayoc governs activity into quipu, bobbin serves it back into the next
agent's context. Memory for the crew, with provenance instead of vibes.

The first vertical slice is the task lifecycle with decisions attached —
because "what did we decide about X, and why" is the single highest-value
question a crew's memory can answer, and because shantytown's event
architecture already learned (the hard way) exactly which facts an event can
honestly carry.

## The method

Inherited from the ontology-engineering tradition and this stack's own
planning docs, and non-negotiable:

- **Competency questions before classes.** Every ontology change is justified
  by a question an agent needs answered, and the question set is the test
  suite.
- **Vertical slices before breadth.** One workflow modeled end-to-end and
  validated against real activity, before any widening.
- **Grow, don't design.** The ontology grows from real agent queries under
  governance; a comprehensive upfront model would never ship and would be
  wrong anyway.
- **Contested is first-class.** Disagreement between sources is recorded as
  contested, queryable as contested, and resolved explicitly — never silently
  overwritten by whoever wrote last.

## Where it ends up

A shelf of packs: `core.pendant.db` (the bootstrap vocabulary), `crew.pendant.db` (the
agentic-coding domain), and eventually others — each versioned, hash-cited,
carrying its own competency queries and recommended trust posture. An agent
team anywhere in the stack attaches the packs it needs and inherits not just
facts but the discipline that produced them.
