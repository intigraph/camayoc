# Delivery emitter catalog

[`delivery-emitters.json`](delivery-emitters.json) is graph data in Quipu's
episode format. Its typed `Tool` records describe supported Camayoc adapters:
tool, event standard, invocation, support status and public documentation.
They are documentation records, not an installation or scheduling manifest.

The README table is a projection of these records. The renderer ingests the
catalog into a fresh disposable local Quipu store, queries only the catalog's
exact identities, validates complete results and renders the table. It does
not contact a server, read credentials, activate an adapter or alter an
existing database. The embedded digest identifies the source catalog bytes.

With a Quipu CLI installed:

```sh
python3 scripts/render-delivery-catalog.py --write
python3 scripts/render-delivery-catalog.py --check
```

Without either flag, the generated block goes to stdout. `--readme PATH`
projects the same block into another README with its own stack heading.
Existing content outside the marked catalog block is preserved; ambiguous
markers and a changed table refuse the check. Add or change catalog records
rather than maintaining a second table by hand.

Deployment emitters have success and failure fixtures in
[`tests/test_actions_emitter.py`](../tests/test_actions_emitter.py),
[`tests/test_ansible_emitter.py`](../tests/test_ansible_emitter.py),
[`tests/test_terraform_emitter.py`](../tests/test_terraform_emitter.py) and
[`tests/test_ingest_cdevents.py`](../tests/test_ingest_cdevents.py).
GitHub lifecycle controls are in
[`tests/test_github_changes_emitter.py`](../tests/test_github_changes_emitter.py).

Supported source is separate from installed, armed and observed behavior.
Deployment frequency, failure rate and restore time require deployment events;
lead time additionally needs change history linked to a successful deployment.
A missing event family or incomplete time window is a coverage limitation,
never a measured zero.
