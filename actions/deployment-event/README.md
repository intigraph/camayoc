# Deployment event Action

Records a deploy job's outcome in Quipu, using the
[CDEvents](https://cdevents.dev) `pipelineRun.finished` standard. Camayoc maps
it to a `DeploymentEvent`, so deployment frequency and change failure rate come
from the graph.

```yaml
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      # ... your deploy steps ...
      - uses: scbrown/camayoc/actions/deployment-event@main
        if: always()
        with:
          quipu-url: ${{ vars.QUIPU_URL }}
          quipu-token: ${{ secrets.QUIPU_TOKEN }}
          service: web
          environment: production
```

Pin `@main` to a commit or tag you have reviewed.

## What it records

| Job status | Event outcome | Artifacts |
| --- | --- | --- |
| `success` | `success` | attempted, and serving = attempted |
| `failure` | `failure` | the attempted revision only |
| `cancelled`, `skipped` | no event | — |

The artifact defaults to `pkg:github/<owner>/<repo>@<sha>`. Set `artifact`
to a package URL when you deploy something else, such as an image digest.
Each deployment attempt is one event, identified by the run attempt, the job
and an instance. Several deploy jobs in one workflow stay distinct. In a matrix,
pass the leg as `instance` so its record has a stable, readable identity:

```yaml
        with:
          instance: ${{ toJSON(matrix) }}
```

Without `instance`, each execution generates one, so matrix legs still never
collide. A re-run of the workflow is a new attempt.

The event carries only the service, environment, artifact, run id and
outcome. It never carries logs, outputs, variables or the environment.

## Failure handling

A missing record never fails a deploy: the step warns and exits 0. Set
`fail-on-error: "true"` to make it fail instead.

## Requirements

- `python3` on the runner (standard on hosted runners). No other dependencies.
- A Quipu server with Camayoc's delivery shapes loaded (`DeploymentEvent`),
  and a write token for it.
