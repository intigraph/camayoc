"""Ansible callback: one CDEvent per target host for an actual apply.

Writes CloudEvents 1.0 JSON files (CDEvents dev.cdevents.pipelinerun.finished)
to a spool directory. It never sends anything: delivery to Camayoc is a
separate step, so a slow or absent ingest cannot change a play's result.

A host's outcome comes from its play recap, not the process exit code:
failed > 0 or unreachable > 0 is a failure even when the job is green. A host
with no recap gets no event (unknown is never reported as success), and a
--check run emits nothing, because it is not a deployment attempt.

Configure with environment variables (all required; nothing is emitted
unless every one is set):

  CAMAYOC_CDEVENTS_SPOOL     directory to write events into
  CAMAYOC_CDEVENTS_SOURCE    CloudEvents source, e.g. example-ci/ansible
  CAMAYOC_CDEVENTS_SERVICE   stable service id the play deploys
  CAMAYOC_CDEVENTS_ARTIFACT  package URL of the revision being applied

Enable with ANSIBLE_CALLBACKS_ENABLED=cdevents and
ANSIBLE_CALLBACK_PLUGINS=<dir containing this file>.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone

try:
    from ansible.plugins.callback import CallbackBase
except ImportError:  # build()/spool() stay usable, and testable, without Ansible
    CallbackBase = object

DOCUMENTATION = """
    name: cdevents
    type: aggregate
    short_description: write one CDEvents pipelineRun.finished per host for an actual apply
    description:
      - See the module docstring; configuration is by environment variable.
"""

TYPE = "dev.cdevents.pipelinerun.finished.0.2.0"
SPEC = "0.4.1"
_PURL = re.compile(r"^pkg:[a-z]+/")
_RUN_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_ENV = ("CAMAYOC_CDEVENTS_SPOOL", "CAMAYOC_CDEVENTS_SOURCE",
        "CAMAYOC_CDEVENTS_SERVICE", "CAMAYOC_CDEVENTS_ARTIFACT")


def build(host: str, recap: dict, *, source: str, service: str, artifact: str,
          when: datetime, run_id: str) -> dict:
    """The CloudEvent for one host's recap of an actual apply.

    run_id names ONE apply: shared by every host event of that run, distinct
    across runs. It is part of the event id, so two applies of the same
    revision to the same host in the same second stay two events, while
    re-emitting one run's event reproduces the same id."""
    if not isinstance(run_id, str) or not _RUN_ID.match(run_id):
        raise ValueError("run_id must be 1-128 of [A-Za-z0-9._:-]")
    failed = recap.get("failures", 0) + recap.get("unreachable", 0) > 0
    stamp = when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    event_id = hashlib.sha256("\0".join(
        (TYPE, source, service, host, artifact, run_id, stamp)).encode()).hexdigest()[:32]
    custom = {
        # Declares the producer, so Camayoc binds this recap to the outcome.
        "emitter": "ansible",
        "deploymentAttempt": True,
        "runId": run_id,
        "service": service,
        "environment": host,
        "attemptedArtifactId": artifact,
        "recap": {k: int(recap.get(k, 0)) for k in
                  ("ok", "changed", "failures", "unreachable", "skipped", "rescued", "ignored")},
    }
    if not failed:
        # A completed apply of this revision is what now serves on the host.
        custom["servingArtifactId"] = artifact
    cdevent = {
        "context": {"id": event_id, "source": source, "timestamp": stamp,
                    "type": TYPE, "version": SPEC},
        "subject": {"id": f"{service}@{host}", "source": source, "type": "pipelineRun",
                    "content": {"pipelineName": f"ansible apply {service}",
                                "outcome": "failure" if failed else "success"}},
        "customData": custom,
    }
    return {"specversion": "1.0", "id": event_id, "source": source, "type": TYPE,
            "time": stamp, "subject": service, "datacontenttype": "application/json",
            "data": cdevent}


def spool(event: dict, directory: str) -> str:
    """Atomic, durable write of one event; the name sorts by time.

    Re-spooling the same event is a no-op. A DIFFERENT event at an existing
    path is refused, never overwritten, so one event cannot erase another."""
    os.makedirs(directory, exist_ok=True)
    name = f"{event['time'].replace(':', '')}-{event['id']}.json"
    path = os.path.join(directory, name)
    body = json.dumps(event, sort_keys=True)
    if os.path.exists(path):
        with open(path) as fh:
            if fh.read() == body:
                return path
        raise FileExistsError(f"a different event already holds {name}")
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(body)
            fh.flush()
            os.fsync(fh.fileno())
        os.link(tmp, path)  # fails rather than replaces if the name appeared meanwhile
    finally:
        os.unlink(tmp)
    return path


class CallbackModule(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "aggregate"
    CALLBACK_NAME = "cdevents"
    CALLBACK_NEEDS_ENABLED = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.check_mode = False
        # One identity per apply: a caller's trusted run id (e.g. a CI run
        # index), else one generated for this callback instance.
        self.run_id = os.environ.get("CAMAYOC_CDEVENTS_RUN_ID", "").strip() or uuid.uuid4().hex

    def v2_playbook_on_play_start(self, play):
        if getattr(play, "check_mode", False):
            self.check_mode = True

    def v2_playbook_on_stats(self, stats):
        config = {k: os.environ.get(k, "").strip() for k in _ENV}
        if not all(config.values()):
            return
        try:
            from ansible import context
            if context.CLIARGS.get("check"):
                self.check_mode = True
        except Exception:
            pass
        if self.check_mode or not _PURL.match(config["CAMAYOC_CDEVENTS_ARTIFACT"]):
            return
        when = datetime.now(timezone.utc)
        for host in sorted(stats.processed):
            recap = stats.summarize(host)
            event = build(host, recap, source=config["CAMAYOC_CDEVENTS_SOURCE"],
                          service=config["CAMAYOC_CDEVENTS_SERVICE"],
                          artifact=config["CAMAYOC_CDEVENTS_ARTIFACT"], when=when,
                          run_id=self.run_id)
            try:
                spool(event, config["CAMAYOC_CDEVENTS_SPOOL"])
            except OSError as exc:
                self._display.warning(f"cdevents: event for {host} not spooled: {exc}")
