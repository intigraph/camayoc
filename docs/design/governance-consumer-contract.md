# Governance consumer contract

Status: source proposal for review. This specifies a fresh consumer
implementation from the retained governance specification; it claims no
recovered source or historical implementation lineage. No loader, hook, scheduler or enforcement is activated.

Exact resources and new mapping semantics are documented in
[Governance resources](../../governance/README.md). The retained 0.7.0 payload contains a declaration
catalog and one combined profile, not executable policy instances, query sets
or the original loader. New approved alignments are separate from that payload.

## Resource loading

The first implementation will be an explicit, optional Camayoc loader. Ordinary
bootstrap remains unchanged. The caller must select a reviewed artifact,
version, content hash, destination graph and shape name; no latest-version,
ROOT, graph-placement or namespace fallback is permitted. Namespace IRIs stay
byte-identical to the resources. Catalog identity, full direct declaration
coverage, shape identity and version are verified before any write. A count of
arbitrary types is insufficient evidence that the declared class/property types
are present.

The resource path must preserve the native full catalog and single combined
shape resource. Loading alignments requires its own explicit choice and review;
shape loading must not silently load them. Import is a staged resource action,
not approval of any policy instance or consumer activation. Refusal, timeout,
truncation, missing identity and incompatible versions are UNKNOWN/refusal;
none means empty, absent or permission to retry. A repeated load is accepted
only after exact typed presence and resource identity are independently read
back. Error text alone cannot certify idempotence. An unknown instance type
must be rejected as a whole transaction after loading the profile.

Every server request uses a documented Camayoc client category. Credentials
remain in the normal credential path and never enter logs, receipts or test
fixtures. Private verification uses an isolated server and owned scratch files,
with explicit test arming; no daemon installation or live shape-load action.

## Validation and admissibility

SHACL conformance and local import admissibility are separate verdicts. Preserve
severity, focus node, result path and message diagnostics. A conforming foreign
bundle cannot downgrade an existing local rejection. Any rejected member of a
mixed transaction blocks the whole transaction; emit-ready members do not
commit around it. Invalid or unrecognized partitions refuse the whole write.

AdmissionPolicy stays proposed-only. Its ODRL Policy subclass alignment does
not certify complete ODRL serialization or conformance, provide an admitted
issuer, or authorize imported principals. A bare import observation or issuer
record cannot substitute for a policy with permission/prohibition/obligation
rules and trust/evidence restrictions. Record, advise and enforce are distinct
consumer choices; this first loader stages resources only and supplies no
automatic activation choice.

A refines edge points from the revised instruction to its earlier source. It
requires genuine substantial-content revision and preservation/narrowing of
earlier obligations. Mere implication, unrelated strengthening or removal of
an earlier obligation does not satisfy that meaning. Where content semantics
or provenance cannot be established, retain UNKNOWN and require attributed
review; do not pretend regex or SHACL can prove natural-language entailment.
Neither the edge nor its PROV alignment implies supersession, retirement,
precedence, transitivity, an activated policy or a lifted hold.

Contradicts remains unaligned. Its evidence must identify shared scope, target
and time in which both instructions cannot be satisfied together. Different
staging/production contexts are not a conflict merely because verbs differ.
The relation records unresolved incompatibility and selects no winner.

## Native projection compatibility

Native Yupana projection is a separate adapter and acceptance gate. Portable
aliases or loaded vocabulary alone do not prove native consumer behavior. This
matrix describes the independently reviewed native source, not an installed-version
or candidate execution claim. Tier, ambient Mode, exposure, exposure provenance,
structural Policy effect/constraintClass and verification point remain distinct.

| Tier | Exposure and provenance | Native text-plane outcome before Mode ceiling |
| --- | --- | --- |
| warn | Any exposure | Advisory |
| block | Public | Blocking flag |
| block | Internal | Explained warning |
| block | Unknown, a measured answer | Explicit unknown warning |
| block | Unknown, source lookup unreachable with no stand-in | Refusing flag |

Enforce applies the native blocking ceiling; Advise never blocks. Invalid tier
is a projection decode error, not a warn/block default. An invalid main pattern
retains policy-not-evaluated/fail-open diagnostics rather than a proven clean
match. No imported soft/PAA/ATM rule becomes a pre-edit denial through aliasing.
Structural policy classification remains declared-class-first with the native
legacy effect fallback and verification-point routing. No advisory throttle
implementation is claimed.

An absent OPTIONAL exemption means no exemption. Distinguish three stages:

1. A malformed direct path regex in the pure TextRule applies function exempts
   nothing and is reported by its errors function.
2. The governed hook collects those errors before evaluation and returns its
   projection-rules fail-open diagnostic for the governed set. It does not
   continue evaluating otherwise-valid projected rules from that malformed set.
3. A PRESENT shared exemptionSelector with a missing, empty, nonliteral,
   malformed or unresolvable path fails projection; it is not a usable narrowed
   rule. Local and shared multivalued paths must retain their exact membership.

The new loader must reject malformed imports atomically before admitting a new
catalog. That prevents bad input from reaching projection; it does not change
the existing native hook's behavior if such input reaches it. Failed refresh
preserves the last fully admitted structural AND text catalog, marks it stale
truthfully, and preserves the existing exact endpoint/version/age/future-date
gates. No partial plane replacement, stale-as-fresh timestamp or clearing of
local rules is allowed. Successful refresh time remains distinct from changed
policy time. Last-admitted data may be used only under the native gates; absent,
expired or UNKNOWN cache state remains explicitly NOT EVALUATED/UNKNOWN.

Native fields and membership currently use exact legacy TextRule subclass,
regex, enforcementTier and selector IRIs. An explicit reviewed field/class
mapping and actual adapter controls are required for portable resources; do
not rewrite immutable namespace IRIs or infer compatibility from aliases.
Invalid imports cannot remove active local rejection. Any hook-behavior change
requires a separate reviewed implementation, not this resource-loader contract.

## Required executable acceptance

Before source approval of the loader, verify on the exact candidate:

1. Full payload identity and exact typed declaration/profile readback on a fresh
   isolated server; a second load proves exact presence without pretending a
   refusal was a new write.
2. A valid proposed AdmissionPolicy and tolerance endpoints conform. Active
   status, missing claim/targets/status, out-of-range tolerance, and an unknown
   instance type refuse with their diagnostics.
3. Missing files, wrong version/hash, a changed profile and an unreadable or
   truncated response fail before any destination write. A malformed mixed
   transaction commits nothing.
4. New alignment identity is separate; ordinary loading does not imply an
   alignment load or any hook/policy activation.
5. Use the same actual adapter/hook inputs on base and candidate for warn/public,
   block/public under Enforce and Advise, block/internal and BOTH unknown
   provenance arms. Include invalid tier and main pattern; absent direct
   exemption, valid matching/nonmatching and malformed direct paths at pure
   and hook stages; valid selector and PRESENT missing/empty/nonliteral/
   malformed selector; combined local/shared paths; exact/unresolved repository
   and literal same-line marker. Positive controls must show a real blocking
   rule firing and a valid exemption exempting. Preserve local rejection.
6. A valid prior structural AND text catalog plus an invalid/truncated new
   member must retain the previous catalog and honest age, refuse partial
   replacement, and preserve diagnostics. Check fresh, expired, future-dated,
   wrong-version/endpoint and absent cache states. Distinguish refresh time
   from changed-policy time. Hand-built expected JSON is insufficient.
7. Resource input files and real HOME/UID resources remain untouched; tests use
   owned copies and meaningful HOME escape checks. Capture exact source,
   engine/adapter versions and commands; separate source tests from fleet
   installation and natural runtime acceptance.

The original partition layout and historical tests remain unrecovered. Naming
review, independent source review, certification, publication and the existing
production shape-load hold remain separate. This proposal does not grant a
public push, installation, live load or consumer activation.
