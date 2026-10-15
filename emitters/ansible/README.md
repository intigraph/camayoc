# Ansible emitter

An Ansible callback that writes one [CDEvents](https://cdevents.dev)
`pipelineRun.finished` event per target host when a playbook actually applies.
Camayoc ingests the events as `DeploymentEvent` records, so a failed apply
counts toward change failure rate even when the job that ran it is green.

## Enable it

```sh
export ANSIBLE_CALLBACKS_ENABLED=cdevents
export ANSIBLE_CALLBACK_PLUGINS=/path/to/camayoc/emitters/ansible
export CAMAYOC_CDEVENTS_SPOOL=/var/spool/cdevents        # where events are written
export CAMAYOC_CDEVENTS_SOURCE=example-ci/ansible        # your CloudEvents source
export CAMAYOC_CDEVENTS_SERVICE=web                     # the service the play deploys
export CAMAYOC_CDEVENTS_ARTIFACT=pkg:generic/example-iac@<commit>   # what is applied
```

Nothing is emitted unless all four variables are set. Optionally set
`CAMAYOC_CDEVENTS_RUN_ID` to your CI's run or attempt id; otherwise each
playbook run gets a generated one. The run id is part of every event's
identity, so two applies of the same revision to the same host stay two
events, and re-emitting one run's event reproduces the same identity.

## What it reports

| Host recap | Event outcome | Artifacts |
| --- | --- | --- |
| `failed` or `unreachable` above zero | `failure` | the attempted revision only |
| otherwise | `success` | attempted, and serving = attempted |
| no recap for the host | no event | — |
| `--check` run | no event | — |

Each event carries `deploymentAttempt: true`, the target host as its
environment and the host's recap counters. The plugin only writes files.
Deliver them with Camayoc's ingest (`scripts/ingest_cdevents.py --post`), so a
slow or absent ingest never changes a play's result.
