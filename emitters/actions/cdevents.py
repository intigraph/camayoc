#!/usr/bin/env python3
"""GitHub/Forgejo Actions emitter: one CDEvent for a deployment job.

    cdevents.py --status success --service web --environment prod > event.json

Prints one CloudEvents 1.0 JSON object (CDEvents dev.cdevents.pipelinerun.finished)
for a job that deploys, or nothing when the job's status is not a deployment
outcome. `success` is a succeeded deployment and `failure` a failed one;
`cancelled` and `skipped` write no event, because nothing is known to have
been attempted to completion. Exit status is 0 whenever the arguments are
valid, including when no event is printed.

The artifact defaults to the repository at the job's commit
(pkg:github/<owner>/<repo>@<sha>, from GITHUB_REPOSITORY and GITHUB_SHA), and
the run id to GITHUB_RUN_ID-GITHUB_RUN_ATTEMPT, so a re-run is a new attempt.

Identity is one deployment attempt: the run attempt, the job (GITHUB_JOB) and
an instance. Several jobs in one workflow, or several matrix legs of one job,
are distinct attempts even when they deploy the same revision to the same
place. Pass --instance (e.g. the job's matrix as JSON) for a readable, stable
instance; without it each execution generates one, so legs never collide.
The event is built once per step, so re-delivering it keeps its id.
The event carries only the fields below: never logs, outputs, variables or
the environment, because job logs and spooled events are readable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone

TYPE = "dev.cdevents.pipelinerun.finished.0.2.0"
SPEC = "0.4.1"
_PURL = re.compile(r"^pkg:[a-z]+/")
_RUN_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
#: A job status that is a deployment outcome, and the CDEvents word for it.
OUTCOME = {"success": "success", "failure": "failure"}


def build(*, status: str, source: str, service: str, environment: str, artifact: str,
          run_id: str, job: str, instance: str, when: datetime) -> dict | None:
    """The CloudEvent for one deployment job, or None when `status` is not an outcome."""
    outcome = OUTCOME.get(status)
    if outcome is None:
        return None
    for name, value in (("source", source), ("service", service),
                        ("environment", environment), ("job", job), ("instance", instance)):
        if not value.strip():
            raise ValueError(f"{name} must not be empty")
    if not _PURL.match(artifact):
        raise ValueError("artifact must be a package URL (pkg:<type>/...)")
    if not _RUN_ID.match(run_id):
        raise ValueError("run id must be 1-128 of [A-Za-z0-9._:-]")
    stamp = when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    event_id = hashlib.sha256("\0".join(
        (TYPE, source, service, environment, artifact, run_id, job, instance)
    ).encode()).hexdigest()[:32]
    custom = {
        "emitter": "github-actions",
        "deploymentAttempt": True,
        "runId": run_id,
        "job": job,
        "instance": instance,
        "service": service,
        "environment": environment,
        "attemptedArtifactId": artifact,
    }
    if outcome == "success":
        custom["servingArtifactId"] = artifact
    cdevent = {
        "context": {"id": event_id, "source": source, "timestamp": stamp,
                    "type": TYPE, "version": SPEC},
        "subject": {"id": f"{service}@{environment}", "source": source, "type": "pipelineRun",
                    "content": {"pipelineName": f"deploy {service}", "outcome": outcome}},
        "customData": custom,
    }
    return {"specversion": "1.0", "id": event_id, "source": source, "type": TYPE,
            "time": stamp, "subject": service, "datacontenttype": "application/json",
            "data": cdevent}


def main(argv: list[str] | None = None, env: dict | None = None) -> int:
    env = os.environ if env is None else env
    repo, sha = env.get("GITHUB_REPOSITORY", ""), env.get("GITHUB_SHA", "")
    run = env.get("GITHUB_RUN_ID", "")
    attempt = env.get("GITHUB_RUN_ATTEMPT", "1")
    server = env.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--status", required=True, help="the job status (job.status)")
    ap.add_argument("--service", required=True, help="stable id of what this job deploys")
    ap.add_argument("--environment", required=True, help="where it deploys")
    ap.add_argument("--artifact", default=f"pkg:github/{repo}@{sha}" if repo and sha else "",
                    help="package URL of the revision deployed")
    ap.add_argument("--source", default=f"{server}/{repo}/actions" if repo else "",
                    help="CloudEvents source")
    ap.add_argument("--run-id", default=f"{run}-{attempt}" if run else "",
                    help="names this run attempt; part of the event id")
    ap.add_argument("--job", default=env.get("GITHUB_JOB", ""),
                    help="the job id (GITHUB_JOB); part of the event id")
    ap.add_argument("--instance", default="",
                    help="distinguishes matrix legs, e.g. the matrix as JSON; "
                         "generated per execution when omitted")
    args = ap.parse_args(argv)
    try:
        # The id has no clock in it: a re-delivered event for the same attempt
        # keeps its identity; GITHUB_RUN_ATTEMPT separates real re-runs.
        event = build(status=args.status.strip().lower(), source=args.source,
                      service=args.service, environment=args.environment,
                      artifact=args.artifact, run_id=args.run_id, job=args.job,
                      instance=args.instance.strip() or uuid.uuid4().hex,
                      when=datetime.now(timezone.utc))
    except ValueError as exc:
        print(f"cdevents: {exc}", file=sys.stderr)
        return 2
    if event is None:
        print(f"cdevents: job status {args.status!r} is not a deployment outcome; no event",
              file=sys.stderr)
        return 0
    print(json.dumps(event, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
