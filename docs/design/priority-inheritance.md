# Priority inheritance through governed graph rules

> Implementation status: source candidate. No installed policy, workflow,
> consumer behavior or delivery capability is claimed.

The project graph retains declared priorities. The rule catalog derives active
blocking relationships only between schema.org Actions with Potential or Active
status. It follows Quechua blockedOn in the dependency direction, includes
transitive chains, and ignores related or shared-verification relationships.
The stored query takes the minimum over each declared priority and those of
its active dependents. Closing a root or removing an edge must retract the
reasoner's relationship before the next priority reading.

Quipu owns the rule, derivation provenance and withdrawal. A project's inferred
companion supplies the derived relations; consumers explicitly select both
project and companion, verify their pairing and freshness, and report UNKNOWN
on a failed or incomplete reading. They never traverse dependencies themselves
or overwrite declared priority. Historical queries retain historical rank.

Shuttle must read its definition and append-only run events directly from
Quipu, using the existing workflow vocabulary. Its reconciliation steps derive,
verify, record the effective rank and notify the owner for a new inheritance
transition. Clearing inheritance is also a transition. A cycle does not remove
blocking edges or make a blocked item ready; the workflow reports the cycle.
The direct-backed workflow and consumers remain to be implemented and tested.
No local workflow journal becomes the authoritative instruction source.

For P0 work and inherited P0 gates, slow soaks, read-cost/resource benchmarks,
wider coverage proofs and natural-cron waits may follow installation. Exact
change review, a staged named rollback, one smoke check of the actual effect,
credential hygiene and one-way-door authorization remain prerequisites.
Deferred checks stay tracked and owned; source approval never closes them.

Activation requires independent review of the exact source, graph-scoped native
rule tests, project/companion isolation, withdrawal controls, a named staged
rollback, an actual-effect smoke check and the normal reviewed policy-loading
path. P0 read-cost and resource benchmarks may follow installation under the
tracked acceptance rule above. The native baseline
has demonstrated declared P3 to effective P0 and back to P3 after closure.
That fixture is not production or scheduled workflow acceptance.
