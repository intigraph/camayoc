# Releases

## v0.1.12 release preparation

This release adds the dependency-free `cy` / `camayoc` Python wheel alongside
our source archive. All four version declarations (Python project, CLI, plugin
and marketplace) agree on 0.1.12. The release proof builds the wheel from the
verified source archive, installs it in a clean environment away from the source
tree, and exercises both entry points, the packaged primer and generated entity
definitions. `source.json` binds the wheel digest to the same source revision;
`SHA256SUMS` covers both artifacts. The build backend is pinned.

The source delta since v0.1.11 includes Alertmanager ingress, deterministic
release asset checks, repository review-mode queries, receiving-restart recovery,
review closure fixes and Directive deadline isolation. It also includes the
accepted packs design; publication does not implement its later phases or load
new shapes into a running store.

After this preparation merges with green CI, tag that exact main revision
`v0.1.12` and let the Release workflow publish it. Do not tag the preparation
branch: the workflow requires the release revision to be on main. Verify both
checksums and `source.json` from the published release, then install:

```bash
uv tool install https://github.com/intigraph/camayoc/releases/download/v0.1.12/camayoc-0.1.12-py3-none-any.whl
cy --version
```

Expected version: `camayoc 0.1.12`. These install instructions become usable when
the tag's release workflow succeeds. Retain v0.1.11 source artifacts for rollback;
consumer adoption remains a separate pinned upgrade. No PyPI publication is
claimed: the wheel is a GitHub release asset.

## v0.1.11 — published 2026-11-24

Source revision: `78e59e036605b974f958781e611fdacfef47964f`.
[Release](https://github.com/intigraph/camayoc/releases/tag/v0.1.11).
This source release includes GitHub discussion ingress, event-armed review
deadlines, recruitment draining and exact native closure time/reason.
It predates wheel publication.

The [Release run](https://github.com/intigraph/camayoc/actions/runs/45503532973)
published source successfully at 22:50 UTC. Pendant readback failed at 23:30 UTC
with `repository pendant unverified after bounded trusted-producer wait; exit=1`.
GitHub's release asset records show all three pendant assets were uploaded on
2026-11-25 at 04:11:48 UTC, after the bounded wait had ended. This establishes
late asset availability; the underlying producer delay is not established by
that job. The failure did not retract the already published source release.
Do not weaken the verifier, extend waits without evidence, or replay a consumed
producer attempt to make the historical run green. Verify the late assets
against the exact release revision separately. On 2026-11-28, rerunning the
current verifier against `78e59e036605b974f958781e611fdacfef47964f` passed
checksum, provenance, member and share-content checks on those late assets.

## v0.1.10 — published 2026-11-21

[Release](https://github.com/intigraph/camayoc/releases/tag/v0.1.10).
Carries explicit typed Directive traceability deadlines (#269); the Release
workflow, including pendant verification, succeeded. Source archive only.

## v0.1.9 — published 2026-11-21

[Release](https://github.com/intigraph/camayoc/releases/tag/v0.1.9).
Carries the reviewed Directive deadline contract in the release profile (#268)
and the deferred traceability deadline and work-item reconciliation fixes since
v0.1.8. The Release workflow, including pendant verification, succeeded.
Source archive only.

## v0.1.8 release preparation

This source release carries the reviewed `MemberOfShape` change from
[PR254](https://github.com/intigraph/camayoc/pull/254), merged at
`a4c1964ccf441cc19d945c440a07c5ce0d0c2f51`. The release scope is recorded in
[`release-notes/v0.1.8.json`](release-notes/v0.1.8.json).

The software-factory domain pack admits one more `member_of` target: the exact
typed process rulebook `aegis:aegis-crew-process-rules` (a Document). Arbitrary
Documents remain refused. The upstream fixture is re-pinned to Quipu `e715342b`,
whose only change to the pinned file since the previous pin is that same branch;
all NodeShape names are retained. This is a source delta relative to v0.1.7 and
must not be represented as an isomorphic replacement of an installed snapshot.

Use the existing source-release workflow after one independent peer and complete
CI. Verify the published source revision, archive checksum, `source.json` and the
carried compatibility manifest separately. Retain verified v0.1.7 artifacts as the
previous release rollback. Runtime adoption needs a reviewed consumer pin change and
its own admission; this release loads nothing.

## v0.1.7 release preparation

This source release includes the reviewed domain placement and raw alias controls
from [PR231](https://github.com/intigraph/camayoc/pull/231), merged at
`f6372f6e43e762385b0b573c2ca5432afff544c9`. The release scope is recorded in
[`release-notes/v0.1.7.json`](release-notes/v0.1.7.json).

The domain fixture retains all 120 NodeShape names and carries five optional
`CommandDiskImpactObservation` constraints from the pinned upstream source:
`commandClass`, `aggregateState`, `runCount`, `maximumDeltaBytes` and
`p95DeltaBytes`. This is a source delta relative to the prior published bundle;
it must not be represented as an isomorphic replacement of an installed snapshot.
The five additive property/rule files compose to all 655 input triples and remain
inactive. Raw WorkItem/Bead views follow existing class mappings to canonical
Action IDs; calendar Vtodo remains a separate exactMatch-linked node. No legacy
type rewrite or inference activation accompanies publication.

Use the existing source-release workflow after one independent peer and complete
CI. Verify the published source revision, archive checksum, `source.json`, carried
compatibility manifest and repository pendant separately. Retain verified v0.1.6 artifacts as the previous release rollback. Existing
consumers keep their own exact prior pins until separately admitted adoption;
this release does not assert that consumers already run v0.1.6. Publishing immutable assets
cannot be undone by a Git revert: retain prior artifacts and do not overwrite or
reuse a published tag. Runtime adoption needs a reviewed pin/adapter change,
same-SHA lab rehearsal, exact named-set assertions and a proved inverse before
its separately admitted scoped operator apply. No production shape removal is
part of this release.

## v0.1.6 release preparation

Both plugin metadata files declare `0.1.6`. The release revision must also include
the private repository-share query budget correction (#218) and the shared-node
exclusion query optimization (#220). The earlier version-only merge (#219)
predates #220 and is not a suitable release revision.

Publish through the existing Release workflow from the reviewed release
preparation merge, then verify both the source assets and the trusted producer's
repository pendant assets. Source publication alone does not establish pendant
acceptance. Keep existing consumers pinned to v0.1.5 until their separate upgrade
and verification gates pass; do not replay its consumed producer attempt.

Camayoc releases contain the tracked source tree used by the plugin and shell
bootstrap, plus deterministic compatibility files built from that archived source. They do not contain a native Camayoc executable.

A stable version tag matching both plugin metadata files produces these source assets:

- `camayoc-vMAJOR.MINOR.PATCH.tar.gz`: tracked source under one enclosing directory.
- `SHA256SUMS`: the archive's SHA-256 digest and exact filename.
- `source.json`: repository, tag, full source revision, artifact name and digest.

From v0.1.12, the release also carries `camayoc-VERSION-py3-none-any.whl`,
covered by the same `SHA256SUMS` and a `wheel` descriptor in `source.json`.
The source archive and compatibility descriptors retain their existing formats.

Download the selected release assets and verify before extracting:

```bash
sha256sum -c SHA256SUMS
tar -xzf camayoc-v0.1.0.tar.gz
```

The checksum establishes agreement with the published asset. Automated consumers
must also pin the independently reviewed digest and revision; a checksum fetched
from the same release is not an independent source of trust.

The Release workflow accepts an existing stable tag on main, either from its tag
push or an explicit dispatch. Before publication, the shared source-release proof
runs lint, private-key checks, the full test suite and packaging controls. It then
extracts the actual archive and runs bootstrap, repeat-bootstrap and ingest/refusal
controls against disposable loopback Quipu using the same reviewed dependency as
the existing integration job. A missing dependency or failed proof stops publication.
The same proof runs on packaging pull requests without publication permissions.

Packaging reads an exact Git revision. Untracked files and modified checkout bytes
never enter the bundle. Stable versions must agree with both metadata files, and
the output directory must be empty. Repeating the same revision with the workflow's
Python 3.11 runtime produces the same archive bytes. Existing releases are not
overwritten. Publishing a source bundle does not install it or activate a service.

## Domain compatibility artifact

When the archived revision carries the pinned original domain ontology and its
compiler, the same producer adds `compatibility/aegis/` inside the existing source
tarball. Its `manifest.json` names every per-domain file, SHA256, byte count and
shape count. `source.json` adds a `compatibility` descriptor with that manifest's
path and SHA256. No extra release, publisher or automatic loader is introduced.
Older revisions without those inputs retain their original source-only format.

The build uses only archived Git objects, including the frozen upstream fixture;
dirty working files cannot alter its inputs. The compiler requires RDFLib 7.6.0,
restores original target IRIs and proves the independently parsed serialized
union RDF-isomorphic to the pinned upstream graph: 120 NodeShapes. Existing
legacy aliases and rule/property exclusions are not changed by packaging.

A consumer must independently pin the normal archive digest, full source revision
and compatibility manifest digest, verify regular members and all carried file
hashes, then use its normal scoped loader and rollback. A successful package does
not prove live loaded counts, current-data validation, alias-query equivalence or
removal of the old source copies. No tag, release or installed pin is selected by
this source change.
