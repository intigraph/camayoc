# Design: packs and one front door

**Status: accepted, not built (2026-11-28).** Nothing on this page is built
unless a "today" column says so.

Camayoc promises a memory that is checked on the way in. That holds on a store
someone has prepared by hand, and it does not hold for a person starting
fresh. The pieces that keep the promise are spread over one CLI, two MCP
servers and about 115 scripts. This design makes Camayoc one product: a domain
is declared once as a **pack**, and `cy` is the one way to check it, load it
and use it.

<p align="center"><img src="../diagrams/pack-lifecycle.svg" width="900" alt="A pack, from declaration to feedback. An author declares one directory that holds a domain's terms, shapes, queries, questions and commands. Camayoc checks that every question has a query and every command has a test. Quipu loads the pack after a dry run and records the version it holds. Camayoc generates the same commands as a CLI, as MCP tools and as agent guidance. Each call is checked and leaves a receipt. Desire Path groups refusals and missing commands into proposals, and an accepted proposal changes the declaration."/></p>

The commands half of this, where an agent is handed exactly the commands its
work item calls for, is designed in the Capability Layer Design of 2026-11-25
and tracked as epic aegis-ssx5. This page does not repeat it. It covers what
that design stands on, and how both meet the
[Shuttle orchestrator](https://intigraph.github.io/shuttle/design/orchestrator.html).

## What this has to do

| # | requirement | today |
|---|---|---|
| 1 | A new user gets working memory from one command | `scripts/bootstrap.sh` loads the core vocabulary and proves the gate. It does not load the agent-memory shapes, so on that store `cy topic add` and `cy record` are refused. `cy record` also refuses any person or system that is not already in the store, and no `cy` command adds one. |
| 2 | A store holds what the tools that read it expect | Nothing records which Camayoc version a store holds. The live store lacks the stored queries `mem_handoff` and `mem_directives_for`, so the session-start memory anchor fails. On this Mac it failed in all 23 sessions that showed one between 2026-11-04 and 2026-11-28. |
| 3 | Knowledge enters one way, with checks before it is admitted | Each source has its own script. The admission and governance tools run offline only. No path asks Quipu for a dry-run validation before it writes. |
| 4 | Meaning is declared once and every surface is generated from it | True for the five memory entities: one shape and one tool file generate the CLI, the MCP tools, the skill text and the man page. Everything else is hand-written scripts. |
| 5 | Agents get purpose-built commands | Not built. The vocabulary for commands, calls, binds and receipts shipped in Quechua 0.14.0. |
| 6 | Every declaration carries its own test | Of 124 competency questions, 44 have a stored query, 33 could have one and do not, and 47 need vocabulary that is not admitted. Jev's 94% question-mapping score was measured on the earlier 91-question suite. |
| 7 | One vocabulary namespace | Every authored term uses the legacy namespace. The `w3id.org/intigraph` names exist only in unpublished mapping ledgers. |
| 8 | Refusals and misses feed back into the declarations | Desire Path records failed tool calls per host. Nothing carries them into Quipu. |
| 9 | Camayoc fits the rest of the stack | See [Fit with the stack](#fit-with-the-stack). |

Rows 1 and 2 were measured on 2026-11-28: the first on a fresh local store,
the second on the live one.

## The model

| thing | what it is |
|---|---|
| **Pack** | One directory that declares a domain: its terms, shapes, stored queries, competency questions, memory entities and commands, with a manifest that names its version. |
| **Installed pack** | A fact in the store: which pack, which version, which digest, loaded when and by whom. |
| **Surface** | What an agent touches: a `cy` subcommand, an MCP tool, a skill. Each is generated from a pack. |
| **Receipt** | The record of one call: the command, the caller, the outcome and, for a refusal, the reason. |
| **Verdict** | The result of one competency question on one store: passes, fails or cannot tell. |

## A pack, start to finish

1. **Declare.** An author writes or edits a pack. A pack is plain files in a
   repository, so a change is reviewed like code.
2. **Check.** `cy pack check` runs without a store. It refuses a pack whose
   shapes do not parse, whose questions have no query, whose queries name
   terms the pack does not declare, or whose commands have no test.
3. **Load.** `cy pack load` asks Quipu for a dry run, then loads the pack and
   writes the installed-pack fact. An additive change loads directly. A change
   that tightens or removes something goes to a person as a schema proposal.
4. **Generate.** `cy` reads the installed packs and offers their commands as a
   CLI and as MCP tools, with the guidance text an agent reads.
5. **Use.** Each call is checked against the pack's shapes and leaves a
   receipt, whether it was admitted or refused.
6. **Learn.** Refused calls and calls to commands that do not exist reach
   Quipu through Desire Path, grouped by pattern. A repeated pattern becomes a
   proposal against one pack.

`cy pack status` compares a store with the packs it should hold and reports
each difference: a missing query, an older shape, a pack that was never
loaded. Chaski raises that as an alarm. The missing memory queries in
requirement 2 are the kind of gap it reports.

## One front door

`cy` becomes the way to do everything Camayoc does. Scripts stay as the
implementation.

| command | does | replaces |
|---|---|---|
| `cy init` | Finds or starts a store, loads the core and memory packs, registers the planes, adds the first person and agent, and proves the gate. | `scripts/bootstrap.sh`, `scripts/planes.py ensure` |
| `cy entity add NAME --type Person` | Adds a person, a system or an agent. It refuses a name that is close to one that exists and shows the match. | no command today |
| `cy pack check`, `load`, `status` | The pack steps above. | `memory_tools.py --check`, `memory_schema_proposal.py`, the load steps in the bootstrap |
| `cy admit` | One way in for an event or a document: map it, tag it, validate it as a dry run, then write it or quarantine it with the reason. Sources plug in as adapters. | `ingest_cdevents.py`, `github_ingress.py`, `alertmanager_ingress.py` and the other producers |
| `cy ask "QUESTION"` | Maps the question to a stored query, runs it and answers with how each fact was learned. It says so when no query answers the question. | `scripts/competency.py`, the `map_question` tool on its own |
| `cy record`, `cy recall` | Unchanged. | |

Three rules of today's memory tool change with this work:

- **Replacing a record is one write.** Today the new record and the mark on
  the old one are two writes, and a failure between them leaves two current
  records.
- **A preference's key includes its context,** so a chat preference and an
  email preference on one topic do not replace each other.
- **`cy` installs from a tagged release,** not from the head of `main`.

## Questions are the tests

A pack does not load without its questions, and a question is a test.

- An **answer** question has a stored query and a fixture, and its verdict is
  checked in CI.
- A **discovery** question checks that an agent with a need finds the right
  command or query. Jev maps it and must reach the 0.75 floor.
- A **refusal** question names something that must be refused.

A pack's release is blocked when a verdict gets worse. The 33 questions that
could have a query get one, and Jev's score is measured again on all 124.

## How this meets the Shuttle orchestrator

The orchestrator design gives each step a performer, inputs and an output
contract. A pack supplies all three.

| in a Shuttle step | comes from a pack as |
|---|---|
| A code step's performer | A command the pack declares, with its reach and its undo. |
| An output contract of kind `shape` or `ask` | A shape or a stored query the pack ships. |
| An agent step's task pendant | The grant resolved for the step: the pack's commands the agent may call. |
| A step's output | The receipt of the call that produced it. |
| The workflow definition | A file in the same pack as the commands and agent types it uses. |

A deployment then installs a workflow, its commands, its checks and its
vocabulary as one reviewed bundle.

## Fit with the stack

| tool | today | next |
|---|---|---|
| **Quipu** | Loads what the bootstrap or an operator posts. Does not know which Camayoc version it holds. Camayoc calls none of dry-run validation, overlays or policy check. | Holds an installed-pack fact for each pack. `cy pack load` and `cy admit` validate as a dry run before they write. The domain shapes that sit in Quipu move into packs. |
| **Quechua** | Declares shared terms under its own older namespace. Shapes for Decision, Verification and WorkItem exist in more than one repository. | Owns the reusable terms. A pack owns the constraints for its domain, and each class has one shape that says what it means. |
| **Shuttle** | Uses Camayoc's core terms and its monthly graph scheme. | Workflow definitions, commands and output contracts ship in packs. |
| **Yupana** | Reads the crew records graph and loads one Camayoc policy shape in its tests. | Resolves a grant from a work item and names the pack commands it allows. |
| **Shantytown** | Uses Jev for stop decisions, board ranking and duplicate checks, and Camayoc for cost. Its session start does not call `cy`. | Provisions `cy` and the generated MCP tools from the installed packs. A job's briefing lists the commands its grant allows. |
| **Bobbin** | Renders the session-start memory anchor from two stored queries that the live store lacks. | Reads the anchor through `cy`, so one tool owns how memory is read. |
| **Chaski** | Runs Camayoc's directive adapters as scheduled jobs and routes on its GitHub records. | Alarms on pack drift. Its reactions ship in packs. |
| **Seeds** | Validates work items against a copy of Camayoc's work-item shape. | Takes that shape from the work pack. |
| **Rikuy** | Reads Camayoc's deployment, release and change graphs. | Shows pack status and question verdicts on the brief. |
| **Desire Path** | No connection. | Carries refusals and missing commands into Quipu, grouped by pattern. |
| **Tambo** | Installs a pinned release and runs the bootstrap against a scratch store. | Runs `cy init`, then records and recalls a decision as its proof. |

## Plan

Each phase ships on its own.

| phase | what ships | tracked work it serves |
|---|---|---|
| **0. Keep today's promise** | `cy init` and `cy entity add`. The memory queries loaded on the live store. `cy` released as a tagged wheel. The tests that run `cy` against a real Quipu block a merge. The book's broken links and stale counts fixed. | aegis-r4f0, aegis-9uq |
| **1. Packs** | The manifest and `cy pack check`, `load` and `status`. Core, memory, delivery and GitHub become the first four packs. The drift alarm. Names move to `w3id.org/intigraph` when a pack loads. | aegis-kru9, aegis-m5s.3, aegis-55ya |
| **2. One way in** | `cy admit` with dry-run validation and quarantine. The existing producers become adapters. | aegis-ov41, aegis-okdh |
| **3. Questions as tests** | `cy ask`. Verdicts gate a pack's release. The 33 unwritten queries. Jev measured on 124 questions. | |
| **4. Commands** | Commands declared in packs and generated as CLI and MCP, receipts, grants in observe mode, and Desire Path as the feedback path. | aegis-ssx5 and its five children |
| **5. Workflows in packs** | Shuttle definitions ship in packs, and pack commands perform code steps. | aegis-77y |

## Decisions (ruled 2026-11-28)

| # | decision | ruling |
|---|---|---|
| 1 | Whether the pack is the unit and `cy` the only front door | Yes. Everything Camayoc owns moves into packs, and every user-facing script becomes a `cy` subcommand. It extends the generator that already produces the memory commands. |
| 2 | How a first person or system gets into a store | `cy init` adds the first person and agent, and `cy entity add` adds more and refuses near-duplicates. `cy record` never creates a name it does not know, because that would fill the store with misspelled duplicates. |
| 3 | What loads without a person | An additive change loads after its dry run passes: a new query, a new optional property, a new class under a governed parent. A change that tightens or removes goes to a person. This follows the 2026-11-06 ruling that Camayoc should make adding shapes easier and not wait on one person. |
| 4 | What goes first | Phase 0 now, as small items under aegis-a1sn, because it repairs things that are broken today. Phases 1 to 3 queue behind the three milestones. Phase 4 keeps the rank epic aegis-ssx5 already has. |

## What stays the same

- `cy record` and `cy recall` keep their flags and their refusals.
- Every record still says whether it was observed, declared or inferred, and
  inferred never overrides declared.
- The gate proof stays. `cy init` reports a store ready only after the store
  has refused a record that does not say how it was learned.
