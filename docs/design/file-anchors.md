# File and entity anchors: pending profile and reference evaluator

Status: source prototype for `aegis-eno48m`, not an installed feature. No schema,
subscription, scheduled job, public endpoint or editor hook is enabled here.
`file-anchors-proposal.ttl` is **pending schema proposal38**, not a shape to load.

## One relationship, one baseline

A hash on an entity cannot identify which of its several source files was
checked. Give each directional binding its own IRI and exact source/target
(graph, entity) pair, SHA256 baseline, fingerprint recipe, source-log epoch and
committed baseline version. A file-to-entity binding requests review of its
target entity. An entity-to-file binding requests review of its target document.
Two bindings can intentionally have different baselines. Reviewing one does not
clear another. Unknown owners remain absent; no default agent is invented.

The proposed `anchoredTo`/`anchors` OWL inverse is only a navigational edge.
It neither assigns a fingerprint nor proves an update obligation. The writer
must atomically publish that edge and its binding, with the selected graph's
ownership and current-byte proof. Materialize this explicit inverse only.
Never materialize an inverse of all mention edges. Consumers must explicitly
select the registered source and its companion inferred graph when reading
materialized inverses; default ROOT or a variable GRAPH clause is not an
inference-coverage guarantee. A bound reverse lookup of the asserted relation
is an alternative that needs no materialized copy. An inverse cannot mean
"automatically edit the other end"; every action is an advisory review request.

The existing `evidenceSource` property describes selector/predicate evidence
(grammar, regex or equivalent), not a file-link relation. `evidenceHash` binds
what a verifier judged, not a file's current version. Do not overload them.

## Incremental evaluator

`scripts/anchor_changes.py` implements a pure, network-free reference evaluator.
A provider supplies exact graph-scoped, committed current fingerprints; it must
not substitute an HTTP fetch, local arbitrary path read, model guess or wall
clock. Missing versions and changed recipes/epochs are UNKNOWN. An older
snapshot than the baseline is UNKNOWN. Only an authoritative tombstone proves
deletion. Equal hashes are NOT_DUE; unequal hashes or explicit deletion are DUE.

`route` accepts at most64 changed subjects and resolves only their bound inverse
binding lists. Idle input makes zero calls. Incomplete/truncated lookups and
fanout above64 refuse the entire batch so the caller retains its cursor and
retries/splits through the existing durable protocol. This function never
advances a cursor. Binding-edge removal also needs the committed old_value to
route its former target; that integration is still owed.

The deterministic event identity includes binding IRI, both graph/entity pairs,
direction, baseline/current hashes, recipe, epoch, source change transaction and
tombstone status. The transaction is when the fingerprint changed, not when it
was read. Replaying a committed change yields the same ID. Returning to the
same bytes in a later transaction yields a new occurrence. Do not hash labels,
merge unrelated entity IRIs or use `asOf` as identity. A source reference is an
opaque exact IRI; it is never fetched automatically.

## Existing delivery machinery and remaining gates

Camayoc's `change_adapter.py` and Chaski's `ChangeRunner` already provide
changed-subject routing, a durable cursor/inbox, UNKNOWN retention and an atomic
verdict/outbox transaction. Reuse these; this prototype adds no second queue,
scheduler, applier or periodic full-entity scan. An eventual bounded initial
binding catalogue is separate from incremental operation and needs keyset
pagination/overflow proof, not an unbounded `full` run.

Before coupling the evaluator:

1. Accept or revise the governed binding schema, and land the accepted shape in
   Quipu's source files. Proposal acceptance alone cannot survive redeployment.
2. Specify the publisher's file-hash predicate/recipe and atomic commit token,
   plus Quipu's graph-scoped canonical entity fingerprint and epoch. Pin exact
   returned IRIs; do not infer final URLs from prefixes.
3. Specify immutable evidence delivery. Chaski's current generic outbox carries
   `item`, recipient and an evidence digest, but drops arbitrary adapter fields.
   It does **not** currently emit this prototype's old/new hashes and target
   pair. A binding key is distinct from its advisory target. Preserve that
   distinction in the governed firing/evidence contract before connecting a
   bead filer or editor nudge; do not silently change generic payload semantics.
4. Add a gated graph reader and change-adapter description/routing branch with
   old-value deletion coverage, replay/crash/overflow controls and a private
   real Chaski outbox round trip. No live emitter registration is included here.
5. For document mentions, resolve `inDocument` and inverse mentions only for
   changed entities. A mention alone has no baseline hash. Require the document's
   observed entity version or report UNKNOWN; do not infer "stale" from every
   historical mention, or route code chunks as editable documentation.
6. Add Yupana edit-time and read-time consumers with explicit source coverage,
   target permissions, acknowledgement and false-positive controls. Acknowledging
   one firing does not alter the anchor baseline automatically.

No live runtime gate is waived by these private controls. Parent acceptance
needs actual source delivery, the chosen publisher/reader contract, source and
consumer tests, then an explicitly authorized installation and same-probe smoke.
Rollback disables the new subscription/consumers and reverts its source change;
existing anchor/firing evidence is retained. This source prototype's inverse is
its four added files; no existing adapter behavior is changed.
