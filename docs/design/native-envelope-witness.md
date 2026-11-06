# Native envelope failure witness

Implementation status: source diagnostics only. Runtime adoption is separately
held. This metadata does not establish an ingress cause or authorize replay.

The native tick receipt adds `envelope_witness`, schema
`native-envelope-witness-v1`, bound to its existing `attempted_item`.
The existing `item` still names the last verified success. Each attempt resets
the witness and control summary before any native read or stored-envelope decode;
a feed failure before an attempt also starts with unknown envelope evidence.

- `graph`: the request's explicit graph after `projection.safe_iri` validation,
  bounded to 2048 printable ASCII characters. Invalid or unavailable graphs are
  `UNKNOWN`; diagnostic validation never changes delivery decisions.
- `body_sha256`: SHA-256 of the entire projection **request mapping**, including
  Turtle, graph, snapshot, replacement flag, actor and source. The encoding tag
  `planes-json-sort-keys-v1` means exactly Python
  `json.dumps(request, sort_keys=True).encode()` as used by `planes._post` for JSON
  requests: sorted keys, default spaces, ASCII escaping, UTF-8. It is not the
  envelope's version/description wrapper, control query, or a reconstructed RDF
  serialization. Stored effects hash the retained request without resending it;
  same-version and new-write paths hash the request just built for that attempt.
- `branch`: `stored-readback`, `same-version`, `new-write`, or `UNKNOWN` before
  branch selection. A malformed stored envelope can identify its branch while
  retaining unknown graph/hash.
- `own_write`: `not-attempted`, `attempted` immediately after the existing writer
  counter advances, or `proved` after the existing write-response checks pass.
  This is current-attempt memory only. It does not claim a transport reached the
  server or an effect was committed, and never uses aggregate tick writes.

Missing graph/hash/branch fields contain the literal `UNKNOWN`. No body, query,
private source values, response values or credentials enter the witness. Hashes
are correlation identifiers, not authorization, redaction of the source body,
or proof of a server effect. Existing receipt file publication and permissions
are unchanged. This feature adds no metrics labels or diagnostic archive.

All control/type predicates, call order, budgets, stop rules, journal prepare/ack,
cursor, replay and lost-response behavior remain unchanged. Diagnostic capture
adds no request or write. A future serialization change must version the encoding
tag. Source revisions remain independently recorded by the deployment evidence.

The separate failure-retention proposal preserves timestamped bounded metadata
across later success; it neither captures this envelope nor replaces attribution.
These changes require separate integration and source identities. Rollback is the
normal reviewed inverse of this source delta, retaining all pending effects,
state and receipts; never reset state or resend effects to roll back diagnostics.
A normally admitted original scheduled failure receipt is still required to prove
diagnostic delivery. Source tests and source approval do not release a runtime
hold or establish sustained ingress recovery.
