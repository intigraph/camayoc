#!/usr/bin/env python3
"""Terraform wrapper: one CDEvent for an actual `terraform apply -json`.

    cdevents.py -- terraform apply -json -auto-approve ...

Runs the command unchanged and exits with its exit code. Each machine-readable
line is echoed as its human `@message` so logs stay readable. After the command
exits, one CloudEvents 1.0 JSON file (CDEvents dev.cdevents.pipelinerun.finished)
is written to a spool directory. Nothing is sent: delivery to Camayoc is a
separate step, so a slow or absent ingest cannot change an apply's result, and
any failure to emit is a warning on stderr, never a changed exit code.

The outcome comes from Terraform's own stream as well as its exit code: an
error diagnostic, an `apply_errored` resource, or a non-zero exit is a failure.
An event is written only for a deployment ATTEMPT: at least one resource apply
started or errored, or the change summary reports an applied change. A plan, a
no-change apply, and a run that failed before touching any resource write
nothing, because nothing was deployed.

Configure with environment variables (all required; nothing is emitted
unless every one is set):

  CAMAYOC_CDEVENTS_SPOOL        directory to write events into
  CAMAYOC_CDEVENTS_SOURCE       CloudEvents source, e.g. example-ci/terraform
  CAMAYOC_CDEVENTS_SERVICE      stable service id the configuration deploys
  CAMAYOC_CDEVENTS_ENVIRONMENT  where it deploys, e.g. the workspace or stack
  CAMAYOC_CDEVENTS_ARTIFACT     package URL of the revision being applied

Optionally CAMAYOC_CDEVENTS_RUN_ID names the run (else one is generated).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

TYPE = "dev.cdevents.pipelinerun.finished.0.2.0"
SPEC = "0.4.1"
_PURL = re.compile(r"^pkg:[a-z]+/")
_RUN_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_ENV = ("CAMAYOC_CDEVENTS_SPOOL", "CAMAYOC_CDEVENTS_SOURCE", "CAMAYOC_CDEVENTS_SERVICE",
        "CAMAYOC_CDEVENTS_ENVIRONMENT", "CAMAYOC_CDEVENTS_ARTIFACT")
COUNTERS = ("add", "change", "import", "remove", "errors", "exitNonzero")
#: A resource action's contribution to the change counters.
_ACTION = {"create": ("add",), "update": ("change",), "delete": ("remove",),
           "replace": ("add", "remove")}


def _spool_module():
    # One spool implementation for every emitter: the Ansible callback's.
    path = Path(__file__).resolve().parent.parent / "ansible" / "cdevents.py"
    spec = importlib.util.spec_from_file_location("camayoc_cdevents_spool", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Recap:
    """What one `terraform apply -json` stream says happened."""

    def __init__(self) -> None:
        self.started = 0
        self.errored = 0
        self.error_diagnostics = 0
        self.completed = {"add": 0, "change": 0, "import": 0, "remove": 0}
        self.summary: dict | None = None

    def read(self, line: str) -> None:
        try:
            msg = json.loads(line)
        except ValueError:
            return
        if not isinstance(msg, dict):
            return
        kind = msg.get("type")
        hook = msg.get("hook") if isinstance(msg.get("hook"), dict) else {}
        if kind == "apply_start":
            self.started += 1
        elif kind == "apply_errored":
            self.errored += 1
        elif kind == "apply_complete":
            for counter in _ACTION.get(hook.get("action"), ()):
                self.completed[counter] += 1
        elif kind == "diagnostic":
            diag = msg.get("diagnostic")
            if isinstance(diag, dict) and diag.get("severity") == "error":
                self.error_diagnostics += 1
        elif kind == "change_summary":
            changes = msg.get("changes")
            if isinstance(changes, dict) and changes.get("operation") in ("apply", "destroy"):
                self.summary = changes

    def counters(self, exit_code: int) -> dict:
        changes = self.completed
        if self.summary is not None:
            changes = {k: int(self.summary.get(k, 0) or 0) for k in self.completed}
        return {**changes, "errors": self.error_diagnostics + self.errored,
                "exitNonzero": int(exit_code != 0)}

    def attempted(self) -> bool:
        changed = self.summary is not None and any(
            int(self.summary.get(k, 0) or 0) > 0 for k in self.completed)
        return self.started > 0 or self.errored > 0 or changed


def build(recap: dict, *, source: str, service: str, environment: str, artifact: str,
          when: datetime, run_id: str) -> dict:
    """The CloudEvent for one apply. `recap` is `Recap.counters()`.

    run_id names ONE apply and is part of the event id, so two applies of the
    same revision in the same second stay two events, while re-emitting one
    run's event reproduces the same id."""
    if not isinstance(run_id, str) or not _RUN_ID.match(run_id):
        raise ValueError("run_id must be 1-128 of [A-Za-z0-9._:-]")
    failed = recap.get("errors", 0) + recap.get("exitNonzero", 0) > 0
    stamp = when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    event_id = hashlib.sha256("\0".join(
        (TYPE, source, service, environment, artifact, run_id, stamp)).encode()).hexdigest()[:32]
    custom = {
        # Declares the producer, so Camayoc binds these counters to the outcome.
        "emitter": "terraform",
        "deploymentAttempt": True,
        "runId": run_id,
        "service": service,
        "environment": environment,
        "attemptedArtifactId": artifact,
        "recap": {k: int(recap.get(k, 0)) for k in COUNTERS},
    }
    if not failed:
        # A completed apply of this revision is what the environment now runs.
        custom["servingArtifactId"] = artifact
    cdevent = {
        "context": {"id": event_id, "source": source, "timestamp": stamp,
                    "type": TYPE, "version": SPEC},
        "subject": {"id": f"{service}@{environment}", "source": source, "type": "pipelineRun",
                    "content": {"pipelineName": f"terraform apply {service}",
                                "outcome": "failure" if failed else "success"}},
        "customData": custom,
    }
    return {"specversion": "1.0", "id": event_id, "source": source, "type": TYPE,
            "time": stamp, "subject": service, "datacontenttype": "application/json",
            "data": cdevent}


def _human(line: str) -> str:
    try:
        msg = json.loads(line)
    except ValueError:
        return line
    return msg.get("@message", line) if isinstance(msg, dict) else line


def emit(recap: Recap, exit_code: int, env: dict) -> str | None:
    """Spool the event for this run, or return None when there is none to write."""
    config = {k: env.get(k, "").strip() for k in _ENV}
    if not all(config.values()) or not _PURL.match(config["CAMAYOC_CDEVENTS_ARTIFACT"]):
        return None
    if not recap.attempted():
        return None
    run_id = env.get("CAMAYOC_CDEVENTS_RUN_ID", "").strip() or uuid.uuid4().hex
    event = build(recap.counters(exit_code), source=config["CAMAYOC_CDEVENTS_SOURCE"],
                  service=config["CAMAYOC_CDEVENTS_SERVICE"],
                  environment=config["CAMAYOC_CDEVENTS_ENVIRONMENT"],
                  artifact=config["CAMAYOC_CDEVENTS_ARTIFACT"],
                  when=datetime.now(timezone.utc), run_id=run_id)
    return _spool_module().spool(event, config["CAMAYOC_CDEVENTS_SPOOL"])


def main(argv: list[str]) -> int:
    if argv[:1] == ["--"]:
        argv = argv[1:]
    if not argv:
        print("usage: cdevents.py -- terraform apply -json ...", file=sys.stderr)
        return 2
    json_mode = "-json" in argv
    if not json_mode:
        print("cdevents: no -json in the command; running it without emitting", file=sys.stderr)
    recap = Recap()
    try:
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, text=True, bufsize=1)
    except OSError as exc:
        print(f"cdevents: cannot run {argv[0]}: {exc}", file=sys.stderr)
        return 127
    for line in proc.stdout:
        line = line.rstrip("\n")
        if json_mode:
            recap.read(line)
            print(_human(line), flush=True)
        else:
            print(line, flush=True)
    code = proc.wait()
    if code < 0:  # killed by a signal: report it the way a shell would
        code = 128 - code
    if json_mode:
        try:
            path = emit(recap, code, dict(os.environ))
            if path:
                print(f"cdevents: spooled {path}", file=sys.stderr)
        except Exception as exc:  # emitting must never change the apply's result
            print(f"cdevents: event not spooled: {exc}", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
