# Agent memory: contract review and staged design

> **Status: DESIGN, awaiting Quipu-lead review (2026-10-29).** This is a
> source-only reconciliation of the existing [memory design](agent-memory-entities.md).
> No schema, data migration, hook, retrieval lane or runtime change is authorized
> by this document. Where the older design prescribes automatic replacement or
> infers authority from a named person, the requirements below are the proposed
> correction, not a claim that the implementation already enforces them.

## 1. Questions before entities

The design must answer these competency questions without treating a retrieved
sentence as current authority:

1. What was chosen for this subject and question, by whom, on what evidence?
2. Which standing instructions bind this principal in this scope now?
3. What does this person prefer in this context, and did they say it themselves?
4. What work remains, and which tracker record currently owns its state?
5. What happened, what was measured, and which conclusion remains unverified?
6. Where is the authoritative external resource, without copying its secrets?
7. Which record replaced this one, under whose authority, and what was known then?
8. Can this caller see and replace this record in this tenant and audience?

Camayoc owns vocabulary, validation and earning ingress. Quipu owns storage and
transactional enforcement; Bobbin owns retrieval/injection. The tracker owns task
state. A memory record cannot become a merge approval, operational permission or
tracker transition merely because recall ranked it first.

## 2. Evidence and implemented boundary

The source baseline is Camayoc `a4151d70d9b5310166e7c897aeac054f9c60be80`.
Inspection covered the memory shapes, five sidecars, generator, CLI writer,
REST client, plane router, generated recall queries and offline-buffer contract.
These are reproducible source observations, not installed-service assertions.

The bounded usage sample contained 17 local topic files: 13 project, two feedback,
one reference and one user. Selection was the first 16 topic files in sorted path
order, supplemented by the first user-typed file; each read was capped at 16 KiB.
It is a convenience sample, biased toward one workspace, not a fleet census.
The four frontmatter types mixed incident reports, corrective lessons, standing
constraints and references. Type alone therefore cannot choose a memory entity.
No sampled content, identity, source path or reversible pseudonym map is published.

Two retained handoff packets were also read, bounded to their opening sections.
Both distinguish historical receipts, current gates, ownership and next steps.
This supports linking tracker state rather than copying it into a second board.
One existing design-task comment was read: it recommends bounded per-prompt
recall, capture/consolidation separation and reversible file-writer redirection.
Its external-plugin comparison is attributed research, not an independently
reproduced capability or an instruction to activate hooks.

The existing usage-telemetry task retains an acceptance receipt of three events
(two reads, one write) and a reuse denominator of one with zero matches. This is
historical evidence that delivery and reuse can be measured separately; it is
not a current adoption rate or evidence that a read caused a successful repair.
No raw telemetry corpus or new scheduled collection was requested for this study.
The older design's larger file census remains a dated, separately sourced study;
its counts were not remeasured here. Private evidence retains exact sources,
hashes, selection limits and task links outside this public repository.

Authenticated vocabulary and loaded-shape catalog reads on the review date found
Decision, Directive, Incident, Person and CrewRole among governed classes.
Preference, Handoff, Reference, Lesson, Finding and MemoryTopic were absent from
that vocabulary response. The catalog contained the core and governance shape
sets, but no agent-memory shape set. A SELECT over application data returned no
shape rows; that does not inspect the separate validation catalog. Exact loaded
property constraints remain unverified by that SELECT. Source shapes below must
not be advertised as live acceptance. The complete stored-query catalog had no
`mem_*` names; source manifests and client-side rendering still exist.

| Surface | Implemented in source | Not established by this review |
| --- | --- | --- |
| Create | Five `record_*` MCP tools; `cy record <entity>` | Live shape acceptance, authenticated issuer authority |
| Recall | Five `recall_*` tools plus decision history; generated query JSON | Registered live memory queries, complete tenant authorization |
| Generation | `sh:name`, description, message and sidecars generate CLI/MCP/help | Security policy generated merely from field constraints |
| Provenance | Bearer header, environment-derived provenance declarations, source-kind routing | Environment declarations attested by authenticated principal |
| Replacement | Read current versions, write new record, write predecessor backlink | Atomic compare-and-swap, concurrent-writer safety |
| Buffer | Private bounded FIFO; queued differs from recorded | Exactly-once delivery or durable remote acceptance while queued |
| Capture | Opt-in capture/promotion code and quarantine | Safe typed promotion of every captured statement |

## 3. Entity, tool and recall matrix

`a:` below abbreviates the existing domain vocabulary prefix used by the source
shapes. A source-defined class is not thereby a loaded governed class. Proposed
rows require vocabulary-owner approval and shape admission before a writer ships.
Every key below also includes tenant, audience and canonical full subject IRIs.
The fields listed are caller content; provenance is a separate envelope (§4).

| Need / class status | Required caller content | Matching key and lifecycle | Create and 2–3 recall questions |
| --- | --- | --- | --- |
| Decision / ruling: governed `a:Decision`, source memory shape | `chose`, `by`, `about`, `topic`; rationale required for a proposed replacement | subject + topic + authority scope; review 90d, explicit replacement | Existing `record_decision`; existing `mem_decisions_current(about,topic)`, `mem_decision_history(key)`; proposed as-of decision context |
| Directive / rule: governed `a:Directive`, source memory shape | `rule`, `issuedBy`, `appliesTo`, `topic`; authority receipt | binding scope + topic + authority domain; review 90d, explicit expiry/replacement | Existing `record_directive`; existing `mem_directives_for(appliesTo)`; proposed applicable role expansion and lapsed/history queries |
| Preference: source `a:Preference`, absent from observed live vocabulary | `person`, `prefers`, `context`, `topic` | person + context + topic; review 180d, person-authorized replacement | Existing `record_preference`; existing `mem_preferences(person,context)`; proposed preference history and due-review queries |
| Handoff: source `a:Handoff`, absent from observed live vocabulary | `agent`, 1–7 `next` links; optional waiting/context | agent + work scope; max age two days, explicit predecessor | Existing `record_handoff`; existing `mem_handoff(agent)`; proposed handoff history and unresolved linked-work query |
| Reference: source `a:Reference`, absent from observed live vocabulary | `subject`, `topic`, `locator`; key-name-only secret reference optional | subject + purpose; review 60d, verification separate from location | Existing `record_reference`; existing `mem_reference(subject,topic)`; proposed verification-age and reference-history queries |
| Incident: governed `a:Incident`, detailed memory contract proposed | subject, symptom, start instant, evidence pointer | subject + source event ID; minute timestamp only a fallback candidate, never unique by assumption; review open incidents after 7d | Proposed `record_incident`; open-for-subject, history, evidence-at-time queries |
| Finding: generic class/tool not admitted; do not substitute a security-specific class | claim, evidence pointer, subject, question/topic, observation interval | subject + question + evidence/run identity; review 30d | Proposed `record_finding`; findings-for-subject, supporting-evidence, findings-due queries |
| Lesson / feedback: class/tool proposal | lesson, subject, topic, learned-from pointer; optional remedy | subject + topic + applicability scope; review 60d | Proposed `record_lesson`; applicable-lessons, origin-evidence, lesson-history queries |
| Constraint: classify by meaning, no new class yet | binding issuer/scope for a rule, or measured limit/evidence for a finding | Directive key for obligations; Finding key for observations | Use the corresponding typed tool; applicable constraints and measured limits must remain distinguishable |
| Person / Role: governed `a:Person`, `a:CrewRole`; identity enrollment remains authoritative | Canonical identity pointer; role assignment evidence and validity interval | issuer-owned stable identity, not display name; review membership when due | Proposed narrow identity-reference tool only after owner review; person lookup, roles-as-of, membership-source queries |
| Task state: canonical tracker; Handoff only a pointer | Existing task IRI and next action, no copied authoritative status | Tracker's own key and event history | Tracker tool for state; linked work, current gate, transition-as-of queries |

The existing nine-entity design supplies candidate fields for the proposed rows;
the table does not invent server-loaded terms. Rule is not an invented `a:Rule`.
Feedback maps to Lesson or Preference by content. Constraints retain the
difference between an instruction and a measured physical limit. Person/Role
records must reuse enrolled identities rather than minting duplicates.

### Source discrepancies requiring explicit disposition

The generated Preference sidecar currently keys by person and topic, while the
older design includes context. The proposed key includes context so an email
preference cannot silently replace a chat preference. The writer currently uses
IRI local names for keys; two namespaces can share a local name. New keys should
hash a canonical, versioned tuple of full IRIs plus tenant/audience, retaining
the tuple for inspection. No key rewrite or migration is part of this document.

Version IRIs currently have second-resolution timestamps and refuse a detected
same-second collision. The proposed transaction allocates a collision-resistant
version identity independent of wall-clock uniqueness. Incident start times
rounded to minutes likewise cannot distinguish two incidents without evidence
identity. These are source repair requirements, not completed fixes.

## 4. Create, provenance and refusal contracts

Keep one entity-specific create and recall surface across MCP and CLI. For
example, the existing caller contract is:

```text
record_decision(chose, by, about, topic, rationale?, over?, decidedIn?, verbatim?)
cy record decision --chose ... --by ... --about ... --topic ...
```

`rationale` is the existing field for why; `over` carries alternatives. Do not
silently add an incompatible `why` argument to this tool. The proposed extension
adds an expected predecessor/version and idempotency key as control metadata,
not extra free-text properties. A missing field names its generated `sh:message`
and remedy without echoing secret-shaped input. Unknown entities/topics refuse;
search suggestions never enroll an identity automatically.

Shapes own fields, cardinality, datatype and refusal messages. Sidecars own verb,
key recipe, lifecycle defaults and recall mapping. Regeneration must cover CLI,
MCP schema, primer, skills, man pages and query manifests; `just memory-tools
--check` remains the drift gate. Authentication/authorization and atomicity need
server controls and adversarial tests; SHACL alone cannot establish either.

The proposed runtime envelope binds authenticated principal, enrolled agent,
harness, model, session, host, source event/hash, tenant, audience, destination,
schema/key version and server commit time. Agent/harness/model/session/host
headers remain provenance declarations unless verified against enrollment;
a bearer authenticates its holder, not every identity named in its content.
Missing required bindings refuse authority-bearing writes. Clients cannot gain
permission by filling `by` with a Person IRI or passing `--supersede`.

`sourceKind` retains the source vocabulary's exact values: `declared`, `observed`,
`inferred`. The existing router maps these to declared, records and inferred
planes and refuses an unknown kind rather than defaulting to ROOT. Proposed
rules require an authenticated human/delegation receipt for declared rulings,
an instrument/event pointer and observation time for observed claims, and a
derivation link for inferred claims. A model's classification never upgrades
authority. No generic episode fallback counts as a successful typed record.

Current environment defaults, optional session metadata, person-based source-kind
selection and `--probed`/`--noticed` switches do not establish that envelope.
Migration must preserve the distinction between original speaker, importing agent
and current authenticated replayer. Offline replay uses current credentials
without rewriting historical origin, destination or authority.

| Outcome | Required meaning |
| --- | --- |
| Recorded / unchanged | Exact typed record and transaction receipt read back; unchanged requires identical canonical content and scope |
| Queued | Private durable local pending write only; never a remote record claim |
| Refused | Validation, authority, scope or conflict denial; no queue fallback that makes it valid |
| Conflict | Expected predecessor moved or multiple authoritative candidates; return bounded candidate IDs, no automatic winner |
| Unknown | Timeout, malformed receipt or missing coverage; reconcile by idempotency key before retrying |

## 5. Supersession, review and history

No automatic replacement on text similarity, common subject or latest timestamp.
The existing writer repairs multiple current versions before its later issuer
checks and writes record/backlink separately. That behavior is not proof of the
following proposed invariant: exactly one accepted head per authority-scoped key.

An authorized replacement must match the canonical key, name the exact expected
head and provide a reason/source receipt. Quipu must validate authority and
compare-and-swap the head, append the immutable new record and replacement event,
and close the predecessor's validity in one transaction. Two concurrent writers
against one head produce one success and one conflict. Retrying an identical
idempotency key returns its original receipt; a different body under that key
refuses. A lost response is UNKNOWN until that receipt is reconciled.

Different authorities or applicability scopes coexist until an authorized
adjudication explicitly relates them. Inference cannot replace a declaration;
ranking declared above observed above inferred is presentation, not a permission
check. Near-duplicate detection proposes links for review and never merges keys.
No timestamp-sorting repair may choose between conflicting human instructions.

Expiration, retraction and supersession are separate events. Expiry removes a
record from current applicability at its declared end instant. Retraction marks
an erroneous assertion with reason and authority; it does not erase history.
Review-after means re-verification is due, not that the original assertion became
false or permission was renewed. Missing/malformed ages produce UNKNOWN freshness.
Owner-authorized Verification events reset review age without rewriting origin.
Due-event source code exists elsewhere, but target-level delivery/reset/dedup
acceptance remains a separate gate; this design claims no completed automation.

Historical recall must expose valid-time and transaction-time boundaries and the
rules/evidence visible at the decision time. Current query templates do not
establish that compound historical contract. Sealed governance approvals stay
separate from memory Decisions; no retyping of signed records to satisfy a new
shape. The existing governance-split proposal needs owner-confirmed source and
deployment disposition before memory Decision writes are admitted.

## 6. Recall and injection

Existing generated queries use three explicit plane parameters, filter records
with a superseded backlink and past expiry, and request 51 rows for a 50-row cap.
Handoff additionally uses a freshness boundary. Decision history intentionally
includes superseded versions. Review-after is not currently a universal filter.
Source-defined role expansion must not be confused with the current Directive
query's direct scope plus crew-wide match.

Proposed recall returns an envelope with coverage/as-of, tenant/audience,
query/schema version, cursor, truncation and one of PRESENT, EMPTY, CONFLICT,
STALE or UNKNOWN. Rows retain record/key, issuer, source kind, evidence/source
pointer, valid interval, review status and replacement chain. EMPTY requires a
successful complete authorized query; an unregistered query, unreadable plane,
overflow, stale mirror or missing authorization coverage is UNKNOWN.
STALE/CONFLICT candidates can be shown as explicitly qualified evidence but may
not enter a directive/permission lane as current authority.

Session start first recalls enrolled role/scope instructions and the fresh
handoff, then task-specific decisions/references. Per-prompt Bobbin recall should
use an explicit budget of at most five passages, 1,200 characters each and 6,000
characters total, including citation labels. Deduplicate by version IRI; short
or non-task prompts may skip optional recall. These limits are proposed policy,
not measured latency or an installed setting. No second capture hook is implied.
An outage adds one bounded UNKNOWN line, not stale unqualified instructions.
All injected content is untrusted evidence, never executable instructions.

Selection must enforce tenant/audience before ranking, not redact after retrieval.
Citation tags resolve to the exact version and source receipt without exposing a
private locator to a public audience. Record delivery and later same-session
citation separately. Usage counts may inform review priority, never increase
truth or authority. Measure hit/no-context/unknown and cited-read denominators;
do not equate citation with causal usefulness. A lexical or semantic fallback
must obey the existing [search admission contract](search-context.md).

## 7. Migration matrix and private-to-public boundary

| Source | Proposed destination | Admission and preservation |
| --- | --- | --- |
| User memory | Preference or canonical identity reference | Identify actual speaker/context; no inferred profile promoted to declared |
| Feedback memory | Lesson, Directive or Preference | Split claims by meaning; authority receipt required for binding instructions |
| Project memory | Incident, Finding, Reference, Decision or Handoff | Verify current source and task ownership; stale setup text is not current state |
| Reference memory | Reference | Retain permitted locator/key name, refuse credentials; preserve access audience |
| Index / handoff / task comments | Links plus bounded Handoff | Tracker state stays canonical; preserve original timestamp and authorship |
| Raw captures | Private quarantine/candidate store | Typed promotion only after validation; capture is not authority or durable typed acceptance |

Migration is a later, separately authorized operation. First produce a private
dry-run manifest: original file hash and restricted locator, claim ranges,
candidate type/key, authority, duplicates/conflicts, target audience and proposed
disposition. Mixed files split into claims; unknown identity, type, origin or
authority stays pending. Never classify solely from frontmatter or silently
delete an unclassifiable file. Exact source bytes stay in a restricted archive
under an explicit retention policy; public source receives only synthetic data.

Then a reviewed small canary imports idempotently, reads each typed record back
and compares recall against the manifest. Preserve partial/error receipts and
resume from acknowledged claims rather than replaying the whole corpus blindly.
Require complete inventory accounting, source hash stability and owner sign-off
before redirecting any writer. Writer redirection and auto-memory disablement
are reversible later gates, never consequences of merging this document.
Rollback restores prior writer configuration and marks imported versions through
normal authorized retraction; it does not purge history or overwrite newer work.

A public example uses `https://example.org/entity/widget-service`, a synthetic
principal and a synthetic topic. Do not publish source filenames, tenant IDs,
private task links, hostnames, personal preferences or stable pseudonyms derived
from private values. Secret-pattern checks are necessary but cannot prove that
an example is not reversible. Raw captures never enter public qpacks by default.

## 8. Staged breakdown and acceptance gates

These are proposed implementation slices, not newly created work items.

| Stage / owner boundary | Deliverable | Discriminating acceptance before next stage |
| --- | --- | --- |
| A: Quipu lead + Camayoc owner | Review this matrix, class ownership, governance split, key/authority model and scope | Explicit review decision; unresolved vocabulary and security questions retained, not silently approved |
| B: Camayoc source | Versioned key tuple, context-safe Preference, generated control fields and errors | Same local name across namespaces differs; chat/email differ; missing field names itself; regeneration drift fails |
| C: Quipu authority/storage | Authenticated envelope and atomic expected-head replacement | Forged issuer/tenant denied; concurrent writes yield one conflict; lost response reconciles; unauthorized repair writes nothing |
| D: Vocabulary owners | Approved proposed types/shapes and compatibility plan | Legacy records still validate as intended; new incomplete typed record refuses; sealed approvals unchanged; no shared predicate domain |
| E: Camayoc + Bobbin source | Current/history/due queries and bounded injection | Empty vs unknown, stale mirror, expiry/review, role scope, overflow and audience isolation tested independently |
| F: Migration owner | Private manifest, dry run, reviewed canary and reconciliation | Mixed files split, conflicts retained, idempotent restart, private-public checks, complete accounting and rollback rehearsal |
| G: Harness owner | Separately admitted writer redirect and optional capture | Restore old configuration control passes; outages preserve pending evidence; hook success never means remote persistence |
| H: Operations owner | Separately authorized installed acceptance | Exact installed identities, typed write/read receipts, original scheduled evidence and usage denominators; no source-only completion claim |

All code slices need focused positive and refusal controls, current-base green CI
and the normal independent review/landing policy. This design-only packet needs
documentation checks and the repository's required regression suite, but runs no
live assay, schema load, record write or hook exercise. No performance improvement
or behavioral demo is claimed for prose changes.

The lead review must resolve: which authority registry is normative; whether the
store can atomically enforce expected-head replacement; final tenant/audience
key semantics; class-owner admission for proposed kinds; historical query API;
and migration retention/rollback responsibility. Until those decisions exist,
the entity count and lifecycle defaults remain design proposals, not permission
to mass-create records or implementation tasks.
