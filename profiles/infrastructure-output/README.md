# Infrastructure connector output

Implementation status: optional source contract under review. No default pack
loading, inference, connector execution or production write follows from it.

Validate record revisions separately from the resources they describe. A record
is a PROV Entity, `schema:about` its subject, and `prov:wasGeneratedBy` the
extraction Activity. The Activity `prov:used` the source Entity. Never attach
extraction generation to the physical host. The source locator, exact revision
and digest are separate values; the extractor revision is the Activity identifier.
See [PROV-O](https://www.w3.org/TR/prov-o/).

Each record explicitly names `ConnectorRecordShape` through `dct:conformsTo`.
Closed shapes reject unexpected fields on records, sources, activities and
subjects. This is an allowlist, not secret detection: secrets placed inside an
allowed label remain possible. Adapters must construct fields from approved
source keys and never copy arbitrary input dictionaries or credential values.

The record carries `core:sourceKind` as declared or observed. An inventory entry
is declared configuration; it cannot establish a running process or host. Keep
that projection in its admitted plane and preserve source records. A Terraform
resource declaration and its observed guest remain separate identities.

The canonical term destinations follow the accepted 1299-term crosswalk:
classes for hosts, guests and Ansible objects are in core; TerraformResource,
ReverseProxyRoute, hostname and ctId are in homelab-infra; runs_on, routes_to,
sourceKind and contentSha256 are in core. This profile does not relocate terms,
rewrite legacy IRIs, merge entities or select an infrastructure instance base.

SHACL alone is insufficient for adapter admission. The adapter must bind an
exact expected record/subject set and marker, reject unexpected RDF types,
require one scoped identity and a resolved duplicate decision, parse IP literals
with address-space scope, and check that each record's source is exactly the
source used by its Activity. Marker removal or an unmarked extra subject must
refuse admission. The offline admission helper parses IP literals with Python ipaddress and
requires canonical text plus an IRI address-space scope; SHACL alone does not
prove IP parsing.
Host/route references need admission and identity checks beyond their type.

Validation does not prove that these shapes are loaded, enforced by ingress,
or compatible with the serving vocabulary. Those require separate measured
activation proofs. Connector output stays disabled until those gates pass and
one non-author approves the full crosswalk and output contract.

## Offline admission

Supply an independently prepared manifest binding every exact record IRI to its
subject IRI and scoped identifier. The checker rejects missing/extra markers,
conflicting identities, unknown types and unequal record/input source links.
Its complete subject inventory is bounded to records, their primary subjects,
sources, extraction activities and address nodes, each with its own permitted
types and predicates. Locator and address-space IRIs are terminal references;
they do not authorize statements about their subjects. External host/service
references carry only the required type statements unless independently bound
by another admitted record. Arbitrary URI reachability never expands this
inventory. It then runs the native SHACL engine on the same
retained input bytes. It makes no graph writes, source fetch or runtime claim.

```sh
uv run --locked --script scripts/admit_infrastructure_output.py \
  profiles/infrastructure-output/example.ttl \
  profiles/infrastructure-output/example.manifest.json --quipu /absolute/quipu
```

The manifest is an explicit expected batch, not an authorization receipt. The
connector still needs its existing duplicate gate and admitted destination.

## Recorded scope comparison

[Output](demo-output.log) and [timing](demo-timing.log) are a retained util-linux
terminal recording, not an inline video. They show native SHACL accepting an
unmarked fixture outside its target scope, then exact-batch admission rejecting
that same input. The complete positive control passes both checks. These are
disposable source fixtures, not an installed-before baseline. The recording
binds fixture/shape SHA256 values. The small files remain with this profile.

Download both files and run:

```sh
scriptreplay --log-out demo-output.log --log-timing demo-timing.log
```
