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
refuse admission. The profile currently excludes IP output until that parsed
address contract is implemented; it must not claim IP validation from a regex.
Host/route references need admission and identity checks beyond their type.

Validation does not prove that these shapes are loaded, enforced by ingress,
or compatible with the serving vocabulary. Those require separate measured
activation proofs. Connector output stays disabled until those gates pass and
one non-author approves the full crosswalk and output contract.
