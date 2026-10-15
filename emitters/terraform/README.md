# Terraform emitter

A wrapper around `terraform apply -json` that writes one
[CDEvents](https://cdevents.dev) `pipelineRun.finished` event per actual apply.
Camayoc ingests the event as a `DeploymentEvent`. A failed apply counts toward
change failure rate even when a pipeline step around it is allowed to fail.

## Use it

```sh
export CAMAYOC_CDEVENTS_SPOOL=/var/spool/cdevents          # where events are written
export CAMAYOC_CDEVENTS_SOURCE=example-ci/terraform        # your CloudEvents source
export CAMAYOC_CDEVENTS_SERVICE=infra                      # what the configuration deploys
export CAMAYOC_CDEVENTS_ENVIRONMENT=prod                   # workspace, stack or site
export CAMAYOC_CDEVENTS_ARTIFACT=pkg:generic/example-iac@<commit>   # what is applied
python3 /path/to/camayoc/emitters/terraform/cdevents.py -- \
  terraform apply -json -auto-approve tfplan
```

The wrapper exits with Terraform's exit code. It prints each JSON line's
human `@message`, so logs read like a normal apply. Nothing is emitted unless
all five variables are set and the command includes `-json`. Without `-json`
the output passes through untouched. Optionally set `CAMAYOC_CDEVENTS_RUN_ID`
to your CI's run or attempt id; otherwise each run gets a generated one. The
run id is part of the event's identity.

## What it reports

| The apply | Event outcome | Artifacts |
| --- | --- | --- |
| an error diagnostic, an errored resource, or a non-zero exit | `failure` | the attempted revision only |
| otherwise | `success` | attempted, and serving = attempted |
| a plan, a no-change apply, or an error before any resource was touched | no event | — |

The event carries `deploymentAttempt: true`, the environment and these
counters: `add`, `change`, `import`, `remove` (from the applied change
summary, else tallied from completed resources), `errors` and `exitNonzero`.
Camayoc requires all six and refuses an event whose outcome contradicts them.
The wrapper only writes files. Deliver them with Camayoc's ingest
(`scripts/ingest_cdevents.py --post`), so a slow or absent ingest never
changes an apply's result. A spool failure is a warning on stderr.
