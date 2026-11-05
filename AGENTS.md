# camayoc - Agent Instructions

## Project Overview

The knot-keeper: bootstrap ontology, knowledge ingress discipline, and
knowledge-pack production for the quipu stack. Camayoc owns what facts MEAN
and how they EARN their way into the graph; it is deliberately not a store
(quipu), not a harness (shantytown), and not retrieval (bobbin).

Sibling repos: scbrown/quipu (governed store), scbrown/shantytown (crew
harness), intigraph/yupana (code structure), scbrown/bobbin (retrieval).

## Conventions

- **Competency questions before classes.** No ontology term without a question
  in competency/ that needs it; the question suite is the test suite.
- **Reuse before minting** — aegis:/prov:/quipu:/bobbin: vocabularies first;
  camayoc: only for genuinely unowned terms. Namespaces are parameters, never
  hardcoded hostnames.
- **rdfs:range only, never rdfs:domain** on shared predicates.
- **Facts true at write time; judgments at read time.** Nothing stored that
  decays.
- **Deterministic-first ingress**; model-inferred facts land quarantined in
  low-trust planes, tagged, never masquerading.
- Design docs live in docs/design/ with an implementation-status banner in
  the quipu house style.

## Build Commands

```bash
just check           # markdown lint (grows the competency eval gate)
just test            # metrics slice + gate-probe refusal tests
```

## Git Workflow — trunk-based, straight to `main`

**Work on `main` and push to `main`.** Do not create feature branches, and do
not open pull requests, unless you are explicitly asked for one. Stated by
Stiwi, 2026-08-18.

```bash
git pull --rebase origin main     # before starting, and again before pushing
# ... work, with `just check` and `just test` green ...
git add -A && git commit && git push origin main
```

**What this costs, stated plainly, because the cost is the reason the rules
below exist.** There is no review step between a commit and the history
everyone else pulls, so **the quality gates are the only gate**. A red `main`
is immediately everyone's problem and there is no pull request standing
between you and it.

- **Run the gates before every push, not once at session end.** `just check` and `just test`.
  The pre-push gate this file specifies is not advisory here; it is the
  entire safety net.
- **Never force-push `main`.** If a push is rejected the remote has work you do
  not have; `git pull --rebase` and resolve it. A force-push discards someone
  else's commits silently, which is exactly the failure that has no undo.
- **Prefer small complete commits to one large end-of-session commit.** Each
  one lands live, so each one has to stand on its own.
- **Work that cannot pass the gates does not get pushed.** There is no branch
  to park it on — finish it, or leave it uncommitted and say so at handoff.

**On the managed "Agent Context Profiles" block below.** It sets a
Conservative default that forbids commits and pushes without explicit
instruction, and its Session Completion steps say to report proposed commands
and wait for approval. **Where that block and this section disagree, this
section governs** — the standing instruction above IS the explicit authority
that block asks for, given once so it does not have to be re-granted every
session. Everything else in that block still holds: an active "do not commit"
or "do not push" from the current user still wins, and a blocked push is still
reported with its exact command and error rather than worked around.

## Before Every Push

Run `just check` and `just test`. Do not push on failure. Work is not
complete until `git push` succeeds.

The gate proofs (`scripts/gate_probe.sh`) must be able to FAIL, and
`tests/test_gate_probe.py` is what holds them to it — a probe that cannot
distinguish a refusal from an unreachable store is not a gate.

## Local tracker files

Legacy local tracker state is excluded from the published source tree.
Use the configured project tracker; do not initialize a second store here.
Earlier tracked records remain available in Git history.
