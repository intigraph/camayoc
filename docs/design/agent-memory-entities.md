# Design: agent-memory entities

Status: DESIGN, for review by the quipu lead. Tracked as aegis-gshu.11 under
the epic aegis-gshu. This is the contract that the bobbin recall work
(gshu.2), capture and promotion (gshu.4), the generated tools (gshu.10), the
memory-file redirect (gshu.1) and the auto-memory switch (gshu.8) build on.

Quipu exists to give memory the structure that Claude's file memory lacks.
Memory files are easy to write, and that is the problem: they pile up, go
stale and start to contradict each other. This design replaces them with a
small set of typed camayoc entities. Each entity has one purpose-built create
tool, and recall comes back through bobbin's per-prompt injection. Code does
the capture, promotion through shapes adds the structure, and supersession
replaces pile-up. Once all of that is verified, Claude auto-memory is turned
off.

This repository is public. The measurements below are counts and kinds only,
and no memory-file content appears here.

## 1. What real usage looks like (measured 2026-10-28)

### 1.1 Claude memory files

Source: `~/.claude/projects/*/memory/*.md` on the operator's Mac. The type
comes from the frontmatter `metadata.type`. "Kind" is a manual classification
of each file by what it records.

| Project | Files | feedback | project | reference | user |
|---|---:|---:|---:|---:|---:|
| administrator workspace | 136 | 59 | 55 | 16 | 6 |
| personal project | 19 | 15 | 3 | 0 | 1 |
| gascity city | 6 | 2 | 3 | 0 | 1 |
| workspace root | 3 | 2 | 1 | 0 | 0 |
| **Total** | **164** | **78** | **62** | **16** | **8** |

Five more projects keep a `MEMORY.md` index with no topic files, 145 lines in
total. The four projects above add 4 more `MEMORY.md` indexes, so there are 9
indexes in all. The administrator's index alone is 134 lines and 23 KB, which
comes to about 6k tokens loaded into every session whether or not any of it
is relevant.

| Recurring kind | Files | Bytes | Frontmatter types it hides under | Entity |
|---|---:|---:|---|---|
| Standing order or standing task | 49 | 63 KB | feedback 44, project 5 | Directive |
| Dated handoff | 27 | 169 KB | project 27 | Handoff |
| Writing, format or channel convention of a person | 24 | 28 KB | feedback 18, user 6 | Preference |
| One ruling on a scoped question | 17 | 22 KB | feedback 10, project 5, user 1, reference 1 | Decision |
| Where a system lives and how to reach it | 16 | 31 KB | reference 9, project 7 | Reference |
| Tool limit, gotcha or term meaning | 16 | 21 KB | feedback 6, reference 5, project 4, user 1 | Lesson |
| Current setup of a system | 14 | 30 KB | project 14 | split: Decision 2, Reference 7, board item or retired 5 |
| Who's who | 1 | 2 KB | reference 1 | Person |

Findings that drive the design:

| # | Measurement | Consequence |
|---|---|---|
| F1 | The four frontmatter types do not predict the kind. "feedback" holds four of the kinds. | Type by kind, with one create tool per kind. Never derive a kind from the file type. |
| F2 | Handoffs are 16% of the files and 46% of the bytes (169 of 367 KB). Only the newest one is ever read. Their 539 bullets are mostly item states that duplicate the work board. | Keep one current Handoff per agent that supersedes the last one, and keep item state on the board. |
| F3 | 12 files retire an earlier rule in prose, and the old file stays live. There are 3 live contradictions across projects: 2 compensation rules and 1 disclosure rule. An outbound send guard tripped on one of them on 10/26. | Supersession has to be an edge on a controlled natural key, and recall must drop superseded records. |
| F4 | 85 of 164 files (52%) were older than 21 days by file mtime at 2026-10-28T05:00Z; the median age was 22 days. A re-run hours later counted 83, because mtimes move. | Every entity gets a review age, and the existing `review_due.py` reactor fires on it. |
| F5 | 26 of the 137 non-handoff files match the case-insensitive regex `<secret-store product name>\|api.key\|_KEY\|token\|password\|passkey\|credential\|keychain`. A narrower regex (`<secret-store product name>\|token\|passkey\|api key\|password\|secret`) matches 24. | A Reference stores a location and a key name only, and the tool refuses anything that looks like a secret (section 3). |

### 1.2 What is already in quipu

| Measurement | Value | Consequence |
|---|---|---|
| `aegis:Directive` nodes | 923, all with label and comment. 159 have `derived_from`, 8 `supersedes`, 5 `sourceKind`, and there are 70+ ad hoc predicates | The class exists. Its fields are free-for-all, so the shape must name the fields. |
| `aegis:OperationalRule` (crew rules, `crew_process_rules`) | 137 (58 in `aegis-crew-process-rules`) | Keep these as is. Directive recall unions them in. |
| `aegis:Incident` | 78 (label and comment; `affects` 21, `caused_by` 13) | The class exists, with no lifecycle fields yet. |
| `aegis:Decision` | 50: 1 with `chose`, 0 with `outcome`, 49 with neither | Today's Decision records pass neither Decision shape. No governance decision exists in the graph yet. |
| Untyped ruling episodes (`prov:Activity`, label `stiwi-ruling*`/`stiwi-directive*`) | 34, from 2026-09-02 to 2026-10-27 | This is the de facto capture tier, so promotion starts here. |
| `aegis:Person` | 3 | Keep Person minimal. |
| Stored and compiled `/ask` catalog | 81 entries | None of them recalls a memory entity. `crew_process_rules`, `camayoc_lapsed_directives` and `daily_brief_ask*` are adjacent but are not recall. |

### 1.3 Why the 10/27 Decision write was refused

An administrator agent tried to record a human ruling and failed twice. The
first attempt was refused with `unknown rdf:type a:Rule`: no Rule class
exists. The second attempt, as `aegis:Decision`, drew 25 violations, because
two loaded shape graphs claim `aegis:Decision` with different meanings:

| Owner | Shape | Required fields | Meaning |
|---|---|---|---|
| camayoc `shapes/core.shapes.ttl` (loaded as `camayoc-core`) | `CamayocDecisionShape` | `sourceKind`, `rdfs:label`, `chose`, each with an `sh:message` | A choice made during work (`ontology/core.ttl`) |
| **quipu** `shapes/governance.ttl:740` (loaded as `governance`) | `DecisionShape` | **`outcome` (approve, reject or changes), `by`, `evidenceHash`, each minCount 1 with no `sh:message`** | A signed gate approval, sealed by content hash |

The quipu shape's three minCount fields are the "unnamed" ones: they carry no
message, so the refusal could not say in plain words what was missing. No
agent can satisfy both shapes honestly. Camayoc's own `governance/shapes.ttl`
targets `quechua:Decision` and is not involved.

**Ruling: split the class in quipu, by a named migration.** The memory
Decision keeps `aegis:Decision`: it is camayoc's term, it is the older one,
and `decidedIn`, `chose`, `over`, `rationale` and `supersededBy` already hang
on it. The quipu governance meaning moves to `aegis:GateApproval`. The
migration, owned by the quipu lead:

1. Add `aegis:GateApproval rdfs:subClassOf aegis:DecisionRecord`. Move
   `governance.ttl:50` (`aegis:Decision rdfs:subClassOf aegis:DecisionRecord`)
   to GateApproval. The memory Decision stops being an ADR and a Document.
2. Retarget `DecisionShape` to `aegis:GateApproval`, and give `outcome`, `by`
   and `evidenceHash` an `sh:message` each.
3. **Writers emit only GateApproval:**
   - `src/store/decision_deck/admission.rs:175`, which types the receipt.
   - The remedy strings in `src/governance/guard.rs:586` and `:607` and in
     `src/governance/router.rs:130`, which tell deciders what to sign.
4. **Readers accept both classes for one release:**
   - `src/governance/router.rs:201` and `src/governance/precedent.rs:108`,
     which add `a:GateApproval` beside `a:Decision`, still requiring
     `evidenceHash`, `outcome` and `by`.
   - The Decision Deck stored query in `src/store/decision_deck.rs:35`.
   - `src/rule_answers/judgment_read.rs:52`, which checks the type.

   After that release, the `a:Decision` branch is dropped.
5. **Never retype a sealed record.** `src/governance/decision_seal.rs` digests
   every fact except `signature` and `prov:wasGeneratedBy`, so `rdf:type` is
   inside the seal. Retyping would void the verdict under `verify_recorded`.
   The live graph holds 0 nodes with `evidenceHash` plus `outcome`, so nothing
   needs retyping today. Any created before the migration release stay
   `aegis:Decision` and are read through the dual-class path for as long as
   they exist.
6. **Tests touched:**
   - `src/governance/router_tests.rs`
   - `src/governance/guard_golden_tests.rs` (remedy goldens)
   - `src/governance_tests.rs`
   - `src/rule_answers/judgment_tests.rs`

   Add a test that a pre-migration sealed `aegis:Decision` still verifies and
   still satisfies its request.

SHACL `targetClass` has no plane scope, so there is no interim workaround.
`record_decision` ships after the quipu migration release. Until then,
rulings are captured untyped (section 5), which is never refused, and
promoted once the release lands.

## 2. Entities

Keep 9 entities, merge 3 and drop 1:

- **Merged:** Rule and Directive are one entity. Constraint folds into Lesson
  (3 files are environment limits). Feedback folds into Lesson or Preference
  by content (F1).
- **Dropped:** Role. CrewRole (17 nodes) and a domain graph's Role class
  already exist.
- **Kept with no memory files behind them:** Incident (78 graph nodes; outages
  sit inside handoffs today) and Finding (watch and study output).

### 2.1 Natural keys that dedupe

A natural key is built only from IRIs and registered terms, never from free
text a caller makes up:

| Key part | Source | Unknown value |
|---|---|---|
| `about`, `subject`, `appliesTo`, `person` | An existing entity IRI (host, repo, service, CrewRole, agent, Person). The literal `crew` is short for the crew-wide scope IRI | Refused, with the 3 nearest IRIs from `quipu_resolve_entity` |
| `topic` | A registered `aegis:MemoryTopic` term (label plus aliases; `cy topic add` registers one). Free text resolves through an exact label or alias match | Refused, with the 3 nearest terms and the `cy topic add` call |
| `context` (Preference) | A closed list: email, chat, code-review, document, artifact, form, travel | Refused |
| `startedAt` (Incident) | UTC, truncated to the minute | n/a |

- Every record carries `aegis:naturalKey`, the key IRI:
  `aegis:mem/<kind>/<scope-local-name>/<topic-local-name>`.
- Each version's own IRI is the key IRI plus `/<recordedAt as yyyymmddThhmmssZ>`,
  so versions never collide and supersession links two IRIs.
- If a new record has the same `about` or scope as a current record, a
  different topic, and a label similarity of 0.8 or more, it is written but
  also listed by `mem_conflicts()`. It never silently becomes a fresh key.

### 2.2 Fields, lifecycle, recall

Shared fields are filled on every entity by the tool, never by the caller:
`rdfs:label`, `aegis:naturalKey`, `aegis:sourceKind`, `aegis:recordedAt`,
`aegis:recordedBy` (agent), `prov:wasAssociatedWith` (session),
`aegis:contentHash`, and the plane via `planes.plane_for(sourceKind)`.
Lifecycle uses terms that are already modeled: `aegis:supersedes`,
`aegis:supersededBy`, `aegis:reviewAfter`, `aegis:maxAge`, `aegis:expiresAt`
(aegis-kxjack).

| Entity (class) | Required (caller) | Optional | Natural key | sourceKind / plane | Lifecycle default | Recall queries |
|---|---|---|---|---|---|---|
| **Decision** (`aegis:Decision`) | `chose`, `by` (who ruled), `about`, `topic` | `over` (alternatives), `rationale`, `decidedIn` (board item or session), `verbatim` | `about` + `topic` | declared if `by` is a human; otherwise inferred | Same key supersedes. `reviewAfter` +90d | `mem_decisions_current(about?, topic?)`, `mem_decision_history(key)` |
| **Directive** (`aegis:Directive`) | `rule` (imperative, one sentence), `issuedBy`, `appliesTo` (agent, CrewRole, repo or `crew`), `topic` | `issuedOn`, `verbatim`, `why`, `expiresAt` | `appliesTo` + `topic` | declared | Same key supersedes; a different issuer needs `--supersede` (section 3). `reviewAfter` +90d | `mem_directives_for(agent)` (unions `crew_process_rules`), `mem_directives_lapsed()` (wraps `camayoc_lapsed_directives`) |
| **Preference** (`aegis:Preference`) | `person`, `prefers` (one sentence), `context`, `topic` | `notWhen`, `example`, `verbatim` | `person` + `context` + `topic` | declared (the person said it) or inferred (an agent noticed it) | Same key supersedes. `reviewAfter` +180d | `mem_preferences(person, context?)` |
| **Lesson** (`aegis:Lesson`) | `lesson` (one sentence), `subject`, `topic`, `learnedFrom` (incident, board item, session or URL) | `remedy`, `appliesTo` | `subject` + `topic` | inferred; promotes to declared when a human confirms | Same key supersedes. `reviewAfter` +60d | `mem_lessons(subject?, topic?)` |
| **Reference** (`aegis:Reference`) | `subject`, `topic` (purpose), `locator` (URL, path, command or graph IRI) | `secretRef` (secret-store key name; never a value), `access` (how to authenticate) | `subject` + `topic` | observed when probed; otherwise declared | Same key supersedes. `reviewAfter` +60d | `mem_reference(subject?, topic?)` |
| **Handoff** (`aegis:Handoff`) | `agent`, `next` (1 to 7 next-step lines, each pointing at a board item, PR or entity) | `waitingOn` (per item), `context` (3 lines max) | `agent` | observed | Each write supersedes the last. `maxAge` P2D | `mem_handoff(agent)` |
| **Incident** (`aegis:Incident`) | `subject`, `symptom`, `startedAt` | `cause`, `fix`, `resolvedAt`, `lesson` (links a Lesson) | `subject` + `startedAt` (minute) | observed | A later write on the same key updates the record (adding `resolvedAt`, `cause`). Open until `resolvedAt`; `reviewAfter` +7d while open | `mem_incidents_open(subject?)`, `mem_incidents_for(subject)` |
| **Finding** (`aegis:Finding`) | `claim` (one sentence), `evidence` (URL, commit, query or board item), `about`, `topic` (the question) | `run` (the watch or patrol that produced it), `implication` | `about` + `topic` | observed (measured) or inferred (read or summarized) | A revised claim on the same question supersedes. `reviewAfter` +30d | `mem_findings(about?, since?)` (subsumes `daily_brief_watch_semweb_v1`) |
| **Person** (`aegis:Person`) | `name`, `handle` (one stable public handle) | `role`, `org`, `stance`, `seenIn` | `handle` | declared | `reviewAfter` +180d | `mem_person(handle?)` |

Two queries span every entity: `mem_index_since(tx)` feeds bobbin's mirror
(section 4), and `mem_due()` lists entities past `reviewAfter`, built on
`review_due.py`. `mem_conflicts()` lists the near-match pairs from 2.1 and
the contradictions from section 5.

Every recall query has the same filters:

- It drops anything with `supersededBy`, anything past `expiresAt`, and any
  Handoff past `maxAge`.
- It returns `iri`, `naturalKey`, `kind`, `label`, `body`, `sourceKind`,
  `recordedAt` and `supersedes`.
- It ranks `crew:declared` ahead of `crew:records`, ahead of `crew:inferred`.
- At most 50 rows; the 51st row is an overflow sentinel.

## 3. Create-tool contract (gshu.10)

The shape is the single source of truth for each tool.

- **Shape (source of truth for fields).**
  `shapes/agent-memory.shapes.ttl` holds one NodeShape per entity. Every
  property carries `sh:name` (the argument name), `sh:description` (help
  text) and `sh:message` (the plain-word refusal). A lint fails the build on
  any minCount without an `sh:message`, which is the 10/27 bug class.
- **Sidecar (tool-only facts).** `tools/memory/<entity>.tool.json` holds the
  verb (`record_decision`), the CLI noun (`cy decision`), the natural-key
  parts, the default lifecycle, the sourceKind rule, and one worked example.
  It holds no field definitions.
- **Generators.** One generator per surface, all reading the shape and the
  sidecar:
  - CLI: `camayoc <noun>` with the `cy` alias. It is a Python entry point,
    installed by tambo with `uv tool install`.
  - MCP tools: `record_<entity>` and `recall_<entity>`.
  - Harness tools: a Claude skill with slash commands, and the Codex
    equivalent.
  - Man pages and `--help`, plus `cy prime`.
- **Drift gate.** CI regenerates every surface and fails on any diff.

Defaults the tool fills: the natural key and the version IRI (2.1),
sourceKind (by rule), plane, `recordedAt`, `recordedBy` (`$SHANTY_AGENT`, or
the harness agent id), session, `contentHash`, the lifecycle defaults, and
`decidedIn` (the current board item, when the agent has one claimed).

**Secret refusal.** `scripts/memory_secret_rules.py` checks every
caller-supplied field before the write, and its diagnostics name the field
and the rule, never the value. It has 12 named rules:

- GitHub classic and fine-grained tokens
- `sk-` model API keys
- Stripe live keys
- Slack `xox*` tokens
- AWS access key ids
- Google API keys
- JWTs
- PEM private-key blocks
- Bearer credentials
- Passwords in URL userinfo
- Labeled `password=`/`token:`/`api_key=` assignments

A 13th rule catches high-entropy strings: unlabeled tokens of 32 characters
or more that mix upper case, lower case and digits, with at least 4.2 bits of
entropy per character. Hex digests and git SHAs do not trip it. `secretRef`
must be a key name (`^[A-Za-z][A-Za-z0-9_./-]{1,127}$`).
`tests/test_memory_secret_rules.py` checks that:

- each rule refuses its fixture, without echoing the value;
- IRIs, URLs, digests and SHAs pass;
- a `secretRef` key name passes;
- a `secretRef` that holds a value is refused.

Fixtures are assembled at runtime, so no credential-shaped literal sits in the
repository.

Behavior:

| Situation | Tool response | Exit |
|---|---|---|
| Fields complete, new key | Writes. Prints `recorded <kind> <iri>` | 0 |
| Same natural key exists, same body | No-op. Prints `unchanged <iri>` | 0 |
| Same key, different body | Writes the new version with `supersedes` on it and `supersededBy` on the old one, closing the old interval (never a delete). Prints `superseded <old> -> <new>` | 0 |
| A new declared record would supersede a declared one with a different `by`/`issuedBy` | Refuses: `Not recorded: this replaces a ruling by <who> (<iri>); pass --supersede <iri> to confirm.` | 2 |
| An inferred record would supersede a declared one | Refuses. Inferred never overrides human-declared | 2 |
| Missing field, or an unknown key part (2.1) | One line per field from `sh:message`, e.g. `Not recorded: a decision needs what was chosen (--chose).`, then the example | 2 |
| A field matches a secret rule | `Not recorded: <field> looks like a secret (<rule>); store the secret's key name in --secret-ref instead.` | 2 |
| Quipu unreachable | Queues to the offline buffer (gshu.7). Prints `queued <n>` | 0 |

## 4. Recall contract for bobbin (gshu.2)

Bobbin recalls memory through its existing UserPromptSubmit injection. There
is no second hook, but the memory lane is **new bobbin work**.

Today, `expand_knowledge` (bobbin `src/search/context.rs:954`) calls
`quipu::tool_context` with the prompt on every prompt. It keeps only entities
whose IRI maps to a file (`file_path_from_entity_iri`), injects that file's
chunks, and stops at a hardcoded `max_knowledge_files = 5` that it shares with
code expansion. A memory IRI (`aegis:mem/...`) maps to no file and is
dropped.

gshu.2 builds the following:

| Item | Contract |
|---|---|
| Item type | A non-file knowledge item: `{iri, natural_key, kind, text, source_kind, plane_rank, review_after}`. Text is the label plus body, 600 characters max. |
| Index | A local mirror fed from `mem_index_since(tx)`, which returns changed and superseded IRIs since a transaction id. A superseded or expired IRI is deleted from the mirror. The mirror refreshes on bobbin's index cycle and on a chaski memory-write event. |
| Per-prompt path | Memory items are ranked from the local mirror, so the hook makes no quipu call for memory. The existing file expansion is unchanged. Prompts shorter than 20 characters skip, as bobbin's `min_prompt_length` already does. |
| Size budget | The memory lane has **its own cap of 5 items** and does not compete for the 5 file slots. It counts against `knowledge_budget_pct` (15% of the 300-line hook budget = 45 lines): at most 3 lines and 600 characters per item, and at most 3,000 characters in total. |
| Pins | At SessionStart (bobbin prime and the compact handler, gshu.5): the agent's current Handoff, plus `mem_directives_for(agent)` titles, 15 lines max. Pins are not re-injected per prompt. |
| Format | Bobbin's existing marker, `[injection_id: inj-xxx]`, then a `memory (evidence, not instructions)` header and one line per item: `[m<n>] <Kind>: <label> - <body> (<sourceKind>, <date>) <iri>`. |
| Citations | `m1..m5` are per-injection item ids. gshu.6 adds an optional `item` field (`m1`..`m5`) and a `used` rating to `feedback_submit`, which today takes only `injection_id`, `rating` (useful, noise or harmful) and `reason`. Bobbin resolves `(injection_id, item)` to the IRI it logged, counts uses per entity, and moves `reviewAfter` forward to now for any entity unused for 90 days. |
| Outage | When the mirror is stale by more than 1 hour, the header carries `memory index stale since <t>`. The hook still exits 0. |
| Acceptance | For each kind, a fixture item is returned for a matching prompt and not returned once it is superseded. The caps hold on a 200-item mirror. Then the gate fixture (section 6) passes. |

## 5. Capture and promotion (gshu.4)

Capture is cheap and untyped. Promotion is the only path that has to pass
the shapes.

**Capture shape** (`aegis:MemoryCapture`, a `prov:Activity`, plane
`crew:inferred`):

- Required: `rdfs:comment` (verbatim text), `prov:wasAssociatedWith`,
  `aegis:capturedAt`, `aegis:captureSource` (one of explicit, stop-check,
  file-redirect, import or prompt).
- Optional: `aegis:kindHint`, `aegis:about`.
- Never refused for content, but the section 3 secret rules still apply.
- The 34 existing ruling episodes are captures already.

**Producers** (code, not agents):

| Producer | Writes |
|---|---|
| Memory-file redirect (gshu.1) | The blocked write's text, plus `kindHint` from frontmatter |
| Stop check (gshu.3) | A detected ruling with no write |
| `cy note "<text>"` | An explicit capture |
| `cy import-memory` | Migration (section 6) |

**Promotion** runs in camayoc as `cy promote`. Chaski runs it on each capture
event (debounced 5 minutes), at SessionEnd and every 150 tool calls, and it
can be run on demand.

1. **Dedupe.** The same `contentHash` within 30 days becomes one capture.
2. **Classify.** Deterministic cues come first: a directive marker, or
   "never"/"always" plus a named issuer, gives Directive. A dated
   human-ruling marker with a choice gives Decision. A `handoff-` file gives
   Handoff. The frontmatter type alone is never used (F1). Model
   classification may only *suggest*. Its output stays `inferred` and lands
   in `crew:inferred`.
3. **Fill.** Map the cues to fields, resolve the key parts (2.1), then call
   the entity's create tool. The shapes gate this, exactly as for a direct
   call.
4. **Promotion succeeds:** the typed entity gets `prov:wasDerivedFrom` the
   capture, and the capture gets `aegis:promotedTo`.
5. **Promotion is refused:** the capture keeps `aegis:promotionBlocked` with
   the plain-word missing fields. `cy promote --pending` lists these, and the
   stop check shows the exact `cy` call that would finish the record.
6. **Supersession:** the same natural key supersedes (section 3).
7. **Conflicts:** the same `about` or scope with a contradicting
   `chose`/`rule` under a different key is never auto-superseded. It is
   listed by `mem_conflicts()`, and a decision item is filed for the issuer
   (F3).

## 6. Migration and the auto-memory gate (gshu.8)

`cy import-memory <dir> --dry-run` turns each file into a capture plus a
proposed typed record, then prints a per-file report with the kind, key,
missing fields and conflicts. A second run without `--dry-run` writes the
records. Each source file moves to `memory/archive/`, and nothing is deleted.
The MEMORY.md index becomes a 3-line pointer to `cy recall`.

| Source | Count | Target |
|---|---:|---|
| Directive files | 49 | 49 Directive |
| Preference files | 24 | 24 Preference |
| Decision files | 17 | 17 Decision (the 3 F3 contradictions resolved by the issuer first) |
| Reference files | 16 | 16 Reference (key names only, F5) |
| Lesson files | 16 | 16 Lesson |
| Current-setup notes | 14 | 2 Decision, 7 Reference, 5 to board items or retired |
| Who's-who file | 1 | 1 Person plus 1 Reference |
| Handoff files | 27 | 1 Handoff per agent (newest); 26 archived as files |
| `MEMORY.md` indexes | 9 | Replaced by the pointer. Lines with no topic file (145, in 5 projects) are imported as captures |
| Quipu ruling episodes | 34 | Promoted through section 5 |
| Quipu `aegis:Decision` without `chose` | 49 | Untouched. Their producer owns them; listed for the quipu lead |

Expected result: about 130 typed entities from 164 files, plus 34 promoted
episodes.

**Gate for `autoMemoryEnabled:false`.** This was decided on 10/28, and tambo
flips the setting. All five checks must hold:

1. **Migration complete.** Every non-handoff file is either typed or
   explicitly retired, `mem_conflicts()` is empty, and `cy promote --pending`
   is empty for the imported captures.
2. **Recall verified.** The 20 replay prompts in
   `tests/fixtures/agent-memory/gate-prompts.json` must pass, at least 18 of
   20. Each prompt is 20 characters or more and names one expected natural
   key, and `tests/test_agent_memory_gate_prompts.py` holds the fixture to
   that. A prompt passes when bobbin's injection carries a current record
   with that key, within the section 4 caps. The fixture's superseded
   version must never appear.
3. **Capture live.** tambo verify proves that the redirect hook fires on a
   write under `*/.claude/projects/*/memory/*`, and that the stop check
   fires. A test ruling must become a typed entity within 10 minutes of
   capture, wall clock, through the event-triggered promotion. This holds for
   Claude and Codex (gshu.9).
4. **Pins live.** A fresh session shows the agent's Handoff and Directive
   titles at SessionStart.
5. **Reversible.** tambo writes `autoMemoryEnabled:false` with a marker,
   never overrides a value the user set, and `tambo memory restore` reverts
   the switch.
