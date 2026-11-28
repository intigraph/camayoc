# Aegis shapes split: per-domain Camayoc files

> **Implementation status: design only.** This plan moves no file. It does not
> edit Quipu or Goldblum, and it changes no loaded shape, shape-set name, pin or
> Gold file. The staged source it builds on (`shapes/aegis/`,
> `placement.json`, `inference-placement.json`) already exists and is still
> unloaded. Each step in §5 is a separate reviewed change that is not yet
> written.

Operator direction (2026-10-19): "Let's get those aegis shapes out of quipu now.
We can still refer to these shapes in quipu, and point readers to these uses of
quipu [they] should also be in quipu's mdbook. Seems like we should add them as
separate files in camayoc? Point workitem, beads and seeds as the aka relation
that we use."

The job here is to say which file each shape goes to and to give an order of
changes that keeps the Gold exact-shape-set assert green at every step. That
assert is in goldblum `roles/quipu_server` ("Assert the live shape sets are
EXACTLY the declared ones"). Any change to what loads into Quipu rolls the deploy
back, as it did at 2026-11-26 00:35Z.

## 1. Inventory: Quipu `origin/main` at `6b0ec80`

| Quipu file | SHA256 (prefix) | MANIFEST | Loaded set name | Contents |
| --- | --- | --- | --- | --- |
| `shapes/aegis-ontology.shapes.ttl` | `9f1c93625b5b` | load | `aegis-ontology` | 120 NodeShapes + 4 untargeted PropertyShapes = **124 named shapes**; 98 distinct `sh:targetClass`; 31 `rdfs:subClassOf` axioms; 106 classes mentioned; 1687 triples |
| `shapes/aegis-class-subsumption.rules.ttl` | `783e854a2117` | load | `class-subsumption` | 1 rule (`git_commit_subsumes_commit`); 4 triples |
| `shapes/aegis-properties.ttl` | `2cb9a68bafec` | **exclude** | none | 150 property declarations, 0 shapes; 641 triples |
| `shapes/aegis-rules.ttl` | `997d639e69dd` | **exclude** | none | 1 RuleSet + 2 transitive rules (`depends_on`, `runs_on`); 10 triples |

All four hashes are byte-identical to the pins Camayoc already stages
(`placement.json` `source_sha256`, `inference-placement.json`) and to
`tests/fixtures/aegis-upstream/original-aegis-ontology.ttl`. Upstream has not
drifted since the staging was cut, so the staged placement is still current.

**Only two Gold-visible sets are affected: `aegis-ontology` and
`class-subsumption`.** The property and rule files are excluded in the MANIFEST,
so moving them never touches the exact-set assert. They still have engine
consumers (§2.2).

## 2. ENGINE versus AEGIS

ENGINE means Quipu code or Quipu's own tests read the term, so Quipu must keep
it or a fixture of it. AEGIS means it is homelab-domain vocabulary that Quipu
only stores and validates. In that case it moves to Camayoc.

### 2.1 Shapes and classes (`aegis-ontology`, `class-subsumption`)

- **ENGINE, 1 shape:** `InternalIdentifierPatternShape`. Quipu's egress scrub
  (`src/share_scrub.rs` `outward_scrub_patterns`) reads
  `aegis:InternalIdentifierPattern`. This is already recorded as
  `ENGINE_CODE_REFERENCE` in `placement.json`. It stays in Quipu, along with
  the `InternalIdentifierPattern rdfs:subClassOf TextRule` axiom that
  `tests/aegis_ontology_shapes.rs` asserts.
- **ENGINE vocabulary carried in AEGIS files:** the `quipu:onViolation`
  annotation (1 `reject` and 16 `emit` uses) and the `rule:` RuleSet vocabulary.
  The engine defines what they mean. The domain files only use them, so they
  move with the shapes that carry them.
- **AEGIS, the other 123 shapes and the class-subsumption rule.** These move.

Some Quipu-side consumers are not engine guarantees, but they do pin bytes. The
removal step (§5 step 5) has to retarget them to a fixture:
`tests/aegis_ontology_shapes.rs` (string asserts on the Bead and
InternalIdentifierPattern axioms, plus fixture validation),
`examples/shacl_cost.rs`, `docs/demos/directive-deferral/demo.py` and
`shapes/verify_shape_invariants.py`.

### 2.2 Properties and rules (unloaded; no Gold effect)

`inference-placement.json` already assigns all 150 properties: 69 go to
`inference/software-factory.properties.ttl` and 81 to
`inference/shared.properties.ttl`. It also assigns the 3 rules (1 to
homelab-infra, 1 to software-factory, 2 to shared). One finding is that the
governance-plane sections are **engine-coupled**, because Quipu's
`src/governance_tests.rs` (`every_sarc_property_the_shape_constrains_is_also_described`,
`governance_plane_properties_are_all_described`) reads
`shapes/aegis-properties.ttl`:

| Property section (upstream header) | Count | Classification |
| --- | --- | --- |
| SARC constraint tuple ("A consequence to understand") | 19 | ENGINE-coupled |
| Sealed decisions | 14 | ENGINE-coupled |
| Escalation precedent | 4 | ENGINE-coupled |
| Policy composition | 9 | ENGINE-coupled |
| Selector and Predicate | 8 | ENGINE-coupled |
| Verdict and the root of trust | 34 | ENGINE-coupled |
| The human trust root | 10 | ENGINE-coupled |
| Claimed-linkage verification | 5 | ENGINE-coupled |
| Workflow, Step, Artifact, PlanStep | 10 | ENGINE-coupled |
| Dispatch inventory (SARC I7) | 28 | mixed: dispatch fields ENGINE-coupled; reaction, TextRule-exemption and `aboutWorkItem` fields AEGIS |
| Filesystem collections | 9 | AEGIS (includes `leadFor`) |
| **Total** | **150** | |

Recommendation: before step 5, Quipu keeps a governance-plane property file
(ENGINE) and Camayoc owns the AEGIS sections. This needs a reviewer ruling,
because it narrows what `inference-placement.json` currently gives Camayoc.
Until then the existing assignment stands as source.

## 3. Domain to file table

There is one Camayoc file per domain, under `shapes/aegis/`. Each file becomes
**one declared shape set** with a stable registry name `aegis-<domain>`. A name
is permanent once loaded, because the MANIFEST rules make a rename a schema
migration. The "staged pack" column shows where each shape sits in today's
three-pack staging, so the refinement can be checked against
`placement.json`. `public-term-profiles` rows load in **compatibility form**,
with their original targets as `build_aegis_compatibility.py` emits them. The
public-term retargeting is a separate later change and is not part of this
move.

`tests/test_aegis_shapes_split.py` parses this table and checks that its shape
column is an exact partition of the pinned upstream fixture.

<!-- split-table:begin -->
| Domain | File | Shapes | Classes | Staged pack (count) | Shapes (without `Shape` suffix) |
| --- | --- | --- | --- | --- | --- |
| work-items | `work-items.shapes.ttl` | 6 | 4 | public-term-profiles 2, shared-vocabulary 1, software-factory 3 | AboutWorkItem, BeadLegacy, DailyBriefRow, Implements, WorkItemScope, Workstream |
| crew | `crew.shapes.ttl` | 16 | 9 | public-term-profiles 1, shared-vocabulary 1, software-factory 14 | CrewMember, CrewRole, DesiredCrewShape, DesiredCrewSlot, HasRole, KeeperRole, LeadFor, LeadForKeeper, ManagedBy, MemberOf, Overseer, Polecat, ReportsTo, ResponsibilityDomain, Rig, TraitValue |
| agent-tooling | `agent-tooling.shapes.ttl` | 10 | 10 | public-term-profiles 5, software-factory 5 | AgentTool, CLI, Capability, ClaudeCodeHook, Formula, LocalCommand, MCPServer, Plugin, Skill, Tool |
| directives | `directives.shapes.ttl` | 8 | 6 | software-factory 8 | AppliesTo, DesignPrinciple, Directive, DirectiveTraceability, OperationalRule, Reaction, ReactionFiring, TextRule |
| alerts | `alerts.shapes.ttl` | 6 | 5 | homelab-infra 5, software-factory 1 | AlertRule, Metric, MonitoringProbe, Monitors, Probe, PushgatewayJob |
| knowledge | `knowledge.shapes.ttl` | 12 | 12 | public-term-profiles 6, software-factory 6 | Component, Concept, DataCorpus, DecisionRecord, DesignDoc, Feature, Limitation, MLModel, Observation, OperationalFact, Requirement, Verification |
| incidents | `incidents.shapes.ttl` | 4 | 4 | software-factory 4 | FailureMode, FailurePattern, Incident, SecurityFinding |
| code-production | `code-production.shapes.ttl` | 10 | 6 | public-term-profiles 3, software-factory 7 | AuthoredBy, CIWorkflow, CiJob, CommittedTo, DerivedFrom, GitCommit, GitRepo, Modifies, Script, SoftwareVersion |
| homelab-infra | `homelab-infra.shapes.ttl` | 39 | 30 | homelab-infra 34, public-term-profiles 2, shared-vocabulary 2, software-factory 1 | APIEndpoint, AnsibleGroup, AnsibleRole, BacksUp, BareMetalHost, CommandDiskImpactObservation, ConfigFile, ConfigSetting, ConfiguredIn, ConnectsTo, Credential, CronJob, DatabaseService, DependsOn, DeployedOn, DockerContainer, DockerImage, ExternalService, Host, LXCContainer, NFSExport, NetworkSegment, Owns, ProxmoxNode, RemoteHost, ReverseProxyRoute, RoutesTo, RunsOn, SSHKey, SearchService, Service, StoragePool, SystemdService, SystemdTimer, TerraformResource, TraefikRoute, TriggeredBy, WebApplication, ZFSDataset |
| personal-media | `personal-media.shapes.ttl` | 11 | 11 | personal-media 6, public-term-profiles 4, software-factory 1 | Attraction, FamilyMember, FileCollection, GoogleAccount, MediaLibrary, Movie, Person, TVSeries, ThemePark, ThemeParkVisit, Watchlist |
| shared | `shared-vocabulary.ttl` | 1 | 0 | software-factory 1 | LabelRequired |
| ENGINE | Quipu (fixture here) | 1 | 1 | fixture 1 | InternalIdentifierPattern |
<!-- split-table:end -->

**Before and after counts.** Before, `aegis-ontology` has 124 named shapes, 98
target classes and 31 subclass axioms. After, the eleven Camayoc domain files
plus shared hold 123 shapes and 97 target classes. Quipu ENGINE holds 1 shape
and 1 class: 124 shapes and 98 classes in total. All 31 subclass axioms go to
`shared-vocabulary.ttl`, except the one ENGINE axiom, which is also retained
upstream. The class-subsumption set has 1 rule before and 1 rule in
`inference/software-factory.rules.ttl` after. The union of all files must stay
RDF-isomorphic to the pinned upstream source. The existing
`test_aegis_compatibility_bundle.py` builder checks this for the three-pack
form, and the next slice extends it to the domain form.

Layout notes. Three edge guards that the staging put in software-factory move
to the domain that owns their targets: `FileCollection` goes to personal-media
because `MediaLibrary ⊑ FileCollection`, `Metric` goes to alerts and
`TriggeredBy` goes to homelab-infra. `CommandDiskImpactObservation` stays in
infra even though its parent `Observation` is in knowledge. Subclass axioms
cross files freely because they all sit in `shared-vocabulary.ttl`.

## 4. WorkItem, Bead and Seed: the aka relation

Camayoc already declares this relation (`ontology/core.ttl`,
`shapes/aegis/seeds-profile.json`, `competency/aegis-work-aliases/`). The split
keeps it as it is and does not mint anything new:

| Name | Role | Relation |
| --- | --- | --- |
| `schema:Action` | canonical work-item type; Seeds (`sd`) owns its profile | none, this is the target |
| `aegis:WorkItem` | deprecated legacy stored type | `dcterms:isReplacedBy schema:Action` |
| `aegis:Bead` | deprecated legacy subclass, kept as auditable history | `rdfs:subClassOf aegis:WorkItem` ; `owl:deprecated true` |
| Seeds item (`https://seeds.local/item/<id>`) | the tracker record that the Action projects | the same Action IRI, validated by the Seeds-owned profile |
| `ical:Vtodo` | calendar view of the same work | a separate node, `skos:exactMatch` either direction |

The relation never uses `owl:sameAs` or `owl:equivalentClass`. Upstream showed
that equivalence materialised once at load goes stale (the Commit/GitCommit
history in the class-subsumption file). Readers reach every alias through the
four raw alias CQs. `BeadLegacyShape` (Bead must also be a WorkItem) moves in
`work-items.shapes.ttl`.

## 5. Migration order: Gold stays green at every step

Every step is its own reviewed change. It is rehearsed on the same SHA against
a lab Quipu with production's pins, and it names its rollback before apply, as
described in [`docs/aegis-domain-removal-plan.md`](../aegis-domain-removal-plan.md).
The invariant at each step is that **the Gold declared set list and the sets
actually loaded change in the same Gold commit**, so the exact-set assert never
sees a difference.

0. **This document.** No live change.
1. **Camayoc source: domain files.** Refine the staged packs into the eleven
   domain files in §3. Add a `domain` field to `placement.json`. Extend the
   compatibility builder and the isomorphism test to the domain form. Release
   reviewed artifacts. There is still no load.
2. **Gold change A (additive).** Add the eleven `aegis-<domain>` sets
   (compatibility form) to the declared list and load them, in one Gold change.
   `aegis-ontology` stays loaded and unchanged, and the assert expects old ∪
   new. Measure in the lab before apply. Each of the 123 shape IRIs is then
   loaded twice, so check whether violation reports double and whether
   `reject` and `emit` outcomes are unchanged. **This is UNKNOWN until
   measured.** If duplicate IRIs across sets misbehave, step 2 has to be a
   one-shot swap instead: unload `aegis-ontology` and load the eleven sets in
   the same change.
3. **Gold change B (pin switch).** Point the authoritative pins at the released
   Camayoc artifacts and freeze the Quipu `aegis-ontology` pin at its last
   revision. After this, Quipu edits to the old file no longer ship. For
   `class-subsumption`, **keep the registry name** and switch only its source
   pin to `inference/software-factory.rules.ttl`, whose bytes are identical.
   The names do not change, so the assert is unaffected. Loading the rule
   twice under two names would duplicate a `rule:id` in the startup ruleset.
   The ruleset is snapshotted at server startup, so verify after a restart and
   not after the load call.
4. **Gold change C (remove old set).** Remove `aegis-ontology` from the declared
   list and unload it, in one Gold change. Add a Quipu ENGINE set that carries
   only `InternalIdentifierPatternShape` and its axiom, if the reviewer keeps
   it as a loaded shape. If not, it stays as an engine-tested fixture. Then
   revalidate a full stored-data snapshot against the prior pins.
5. **Quipu PR (delete copies).** Gold no longer loads them, so delete the four
   upstream files and their MANIFEST entries in the same PR, because
   `verify_manifest.py` refuses a stale entry. Retarget the consumers listed
   in §2.1 to fixtures, keep the ENGINE property file (§2.2) and add the
   mdbook pointer (§6). This must come after step 4. If it came earlier, a
   Gold deploy of the new Quipu SHA would find a declared set with no source.

Rollback for any step is to restore the prior Gold pins and re-apply the scoped
`shacl` tags. A source revert alone does not unload a set.

## 6. Quipu mdbook pointer (text for step 5)

Proposed for `docs/book/src/concepts/shacl-validation.md`, under a new
"Domain shapes" heading:

> Quipu ships the SHACL **engine** and only the constraints its own code relies
> on (currently `InternalIdentifierPatternShape`, read by the egress scrub, and
> the governance-plane property declarations). The aegis **domain** shapes (work
> items, crew, agent tooling, directives, alerts, knowledge, incidents, code
> production, homelab infrastructure, personal media) live in Camayoc under
> `shapes/aegis/`, one file and one loaded shape set (`aegis-<domain>`) per
> domain. They remain the shapes a Quipu deployment validates against, loaded
> through `POST /shapes` like any other set, and the examples in this book still
> use them. For what a term means and how a fact earns its way in, read Camayoc's
> `docs/design/aegis-shapes-split.md`. Work items are `schema:Action`, owned by
> the Seeds profile; `aegis:WorkItem` and `aegis:Bead` are deprecated aliases
> reached through `dcterms:isReplacedBy` and `rdfs:subClassOf`.

## 7. Not in scope

This slice does not move any file, edit Quipu or Goldblum, propose or accept
shapes, or activate any load or reasoning. It also does not settle the §2.2
ruling.
