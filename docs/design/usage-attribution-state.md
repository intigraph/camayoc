# Attribution state amendment

The [proposal 43 submitted rationale](usage-attribution-state.contract.txt)
is retained byte for byte, including its proposal 42 lineage paragraph.
The exact revised TTL is `shapes/usage-generations.shapes.ttl`.

The conditional A request shape requires one explicit attribution-state string.
The three parser states round-trip independently; absent, duplicate, wrong-kind,
unknown-enum and contradictory states refuse. Old C records remain compatible.

Proposal acceptance, normal profile deployment and runtime enrollment remain
separate from this reviewed source change.
