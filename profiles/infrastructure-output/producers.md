# Offline infrastructure producers

`scripts/produce_infrastructure.py` reads an exported Ansible inventory JSON
([Ansible inventory script JSON](https://docs.ansible.com/projects/ansible/latest/dev_guide/developing_inventory.html))
or the [Terraform JSON values tree](https://developer.hashicorp.com/terraform/internals/json-format).
It never invokes either tool, loads plugins, fetches a source, or writes ingress.
Use disposable fixtures only until the deployment's address and vocabulary
contracts are admitted. No scheduler is installed.

Camayoc owns the reusable producer. A deployment owns its mapping, inventory
export, source revision and destination identities. In Aegis that configuration
belongs in Gold. There is no default instance namespace or label-to-IRI inference.
The producer requires every exported host or managed resource address to have
an explicit mapping or reasoned exclusion. Terraform data sources are excluded
from the managed-resource plane. Child modules are traversed; Terraform resource
declarations never become observed containers.

For Terraform JSON format major version 1, optional `state_subjects` supplies
separate host/guest identities keyed by deployment-selected names. Each entry
has the ordinary record/subject/identifier/type fields, plus `resource` naming
a separately mapped managed resource, and an approved `label_field` from
`hostname`, `name`, `node_name` or `target_node`. A state guest also names its
mapped `host` and `host_field` (`node_name` or `target_node`). The extracted host
field must equal that host's extracted label; a contradictory placement refuses.
State subjects use the observed plane to describe the retained state snapshot.
They do not establish that a process or guest is currently running. Terraform
resource declarations remain separate subjects in the declared plane. Distinct
mapping keys must have distinct subject IRIs, including across `entities` and
`state_subjects`. Shared identifiers or matching labels cannot authorize aliasing;
any source-alias adjudication belongs in the reviewed deployment mapping first.

A mapping contains `producer` (`ansible` or `terraform`), exact forty-character
`source_revision` and `extractor_revision`, explicit `source_iri`, `activity_iri`
and `source_locator`, and `entities` keyed by inventory hostname or Terraform
resource address. Each entry supplies `record`, `subject`, scoped `identifier`
and canonical class `type`. Ansible guest entries also name an explicitly mapped
`host`; this placement is deployment configuration, not a runtime observation.
Optional `excluded` maps source keys to nonempty reasons. Ansible supports host,
bare-metal host, Proxmox node, LXC guest and declared service objects. It includes
group members even when `_meta.hostvars` has no variables for them.
Optional `service_variables` maps inventory hostnames to a deployment-selected
host variable containing an explicit list of service/unit names. Each creates an
input key `hostname/service/unit`, requiring its own entity mapping, service class
and explicitly mapped host. This is declared role configuration, never an observed
process list. No variable is selected by default. Address and route extraction,
group/role membership edges and executable task interpretation remain outside this
initial producer.

Only approved identity fields and source keys enter the RDF. Arbitrary variables,
resource values, passwords and outputs are not copied. A SHA256 binds the exact
input bytes; each record separately hashes its approved mapping, selected label
and assertion plane. Records link
to the source and extraction activity using the existing output profile.
Repeated input and mapping produce the same RDF graph and manifest; Turtle
serialization byte order is not an idempotency contract.

```sh
uv run --locked --script scripts/produce_infrastructure.py \
  inventory.json mapping.json new-output-directory
```

The directory must not exist. It receives `output.ttl`, `manifest.json` and
`report.json`. The producer checks the exact offline batch inventory before
writing files. Run the separate admission CLI with the native Quipu binary to
check SHACL too. Neither check supplies a duplicate decision or write authority.

Production remains behind the explicit-address capability, loaded governed
canonical classes and identity contract. Reconciliation, retractions, source
replacement and live inventory counts still require a separately admitted
ingress path. No five-guest placement acceptance or running infrastructure
claim follows from these fixture tests.

## Fixture demonstration

[Recording](producer-demo-output.log) and [timing](producer-demo-timing.log)
show the actual CLI generating three inventory records and three Terraform
records (declaration, recorded host and guest). Each batch passes exact-batch
and native SHACL admission, omits
the fixture secret sentinel, and reproduces the same RDF graph on a second run.
The retained fixture revisions are synthetic test values. This demonstrates
local file generation, with no ingress or installed-before baseline.

Replay with `scriptreplay --log-out producer-demo-output.log --log-timing producer-demo-timing.log`.
