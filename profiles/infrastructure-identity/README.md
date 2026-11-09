# Infrastructure identity keys

This opt-in profile defines stable allocation keys for the accepted canonical
classes. It is source only: no installer or ordinary namespace bundle loads it.
The deployment owns its namespace declaration, inventory authority and lifecycle
ledger. Quipu's owner-applied registry and authenticated mint operation allocate
the identity; a connector consumes the returned IRI rather than concatenating
a namespace or deriving its own hash.

| Allocation type | Ordered immutable key |
| --- | --- |
| `core:Host` | Site ID, physical asset ID |
| `core:LXCContainer` | Cluster ID, guest kind, integer VMID, creation incarnation |
| `core:SystemdService` | Host IRI, exact unit name, deployment incarnation |
| `infra:TerraformResource` | State authority ID, workspace ID, resource address, declaration incarnation |
| `crtv:IPAddress` | Address-space IRI, canonical address |
| `core:Document` | Site ID, immutable document ID |

The shape file supplies the exact predicate IRIs, ordered `quipu:keyFields`,
exactly-one typed values and explicit hash strategy. Identifiers must be
authority-issued and immutable. Labels, hostnames, paths, IP assignments and
current placement cannot substitute for those keys. No inventory authority
or real asset ID is allocated by these fixtures.

Mint a physical machine as `Host`. `BareMetalHost` and `ProxmoxNode` are
additional classifications on that same IRI, not separate allocation types.
The deployment's namespace selector must include only the six allocation types.
Other guest kinds and logical service identities need a separately reviewed
type/key contract. The primary-type constraint refuses a Terraform declaration
that is also the observed guest or another primary identity type.

The initial address constraint accepts canonical IPv4 only. Noncanonical IPv4,
IPv6 and zone-qualified forms refuse; an IPv6 canonicalizer remains necessary
before that allocation surface is admitted. Refusal preserves the identity
contract rather than minting multiple spellings of one address.

Renaming a host or document preserves identity. A guest may move between hosts
inside its cluster without changing its key. Reusing a VMID after destruction,
recreating a deployment, or changing a resource declaration's identity creates
a new incarnation with reviewed supersession evidence. Credential rotation
never changes an entity IRI. Allocated key contracts cannot be silently edited.

Before activation, ship and pin these bytes, load the governed profile through
the deployment's normal loader, apply the owner namespace locally, and enroll
the exact dedicated mint-only principal/graph instance. Semantic constraints
also require an admitted rejecting SHACL path; loading shapes while write
validation is disabled is not proof of enforcement. Prove valid mint/repeat and
invalid key/type/graph/principal controls on the actual serving revision. Existing
identities, legacy vocabulary and stored edges are not migrated by this profile.

`tests/test_infrastructure_identity.py` checks all six valid fixtures, missing,
duplicate and wrong-kind keys, mutable aliases, VMID kind/range, primary-type
separation and address refusals. It also runs native SHACL positive and negative
controls when the declared Quipu test binary is available. These are local
fixture proofs, not runtime adoption or a production permission receipt.

## Fixture demonstration

The [recording](demo-output.log) and [timing](demo-timing.log) compare the same
disposable RDF under an empty policy and this candidate profile. A complete Host
passes both; a Host missing its site and a Host aliasing a Terraform declaration
pass the empty policy and refuse under the profile. No prior profile existed at
base `ae79e866f651d6d32a5a96c72891d66283724b07`; this is a schema-fixture
comparison, not an old server versus new server measurement.

Reproduce with `uv run --frozen --python 3.11 --group review python
profiles/infrastructure-identity/demo.py`. Download both logs and replay with
`scriptreplay --log-out demo-output.log --log-timing demo-timing.log`.
The recording shows these candidate shape bytes; no live identity is allocated.
