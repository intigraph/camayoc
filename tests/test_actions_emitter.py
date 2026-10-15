"""The Actions emitter and the reusable deployment-event Action."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
EMITTER = ROOT / "emitters" / "actions" / "cdevents.py"
ACTION = ROOT / "actions" / "deployment-event" / "action.yml"
sys.path.insert(0, str(ROOT / "scripts"))
_spec = importlib.util.spec_from_file_location("ingest_cdevents", ROOT / "scripts/ingest_cdevents.py")
ingest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ingest)

ARTIFACT = "pkg:github/example/web@5e1f0c9d8b7a6f5e4d3c2b1a0f9e8d7c6b5a4f3e"
GH_ENV = {"GITHUB_JOB": "deploy", "GITHUB_REPOSITORY": "example/web", "GITHUB_SHA": ARTIFACT.rsplit("@", 1)[1],
          "GITHUB_RUN_ID": "9001", "GITHUB_RUN_ATTEMPT": "2",
          "GITHUB_SERVER_URL": "https://github.example"}
#: Everything an event may carry. Job logs and spools are readable, so a field
#: outside this list fails the test rather than leaking quietly.
ALLOWED = {"emitter", "deploymentAttempt", "runId", "job", "instance", "service",
           "environment", "attemptedArtifactId", "servingArtifactId"}


def emitter():
    spec = importlib.util.spec_from_file_location("cdevents_actions", EMITTER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def event(status, run_id="9001-1", job="deploy", instance="leg-a"):
    return emitter().build(status=status, source="https://github.example/example/web/actions",
                           service="web", environment="production", artifact=ARTIFACT,
                           run_id=run_id, job=job, instance=instance,
                           when=datetime(2026, 10, 15, 7, 2, 11, tzinfo=timezone.utc))


def props(e):
    return ingest.episode_for(e, actor="t")["nodes"][0]["properties"]


class BuildTests(unittest.TestCase):
    def test_success_serves_the_deployed_revision(self):
        p = props(event("success"))
        self.assertEqual("succeeded", p["deploymentOutcome"])
        self.assertEqual(ARTIFACT, p["artifactId"])
        self.assertEqual("production", p["environment"])

    def test_failure_records_only_the_attempt(self):
        p = props(event("failure"))
        self.assertEqual("failed", p["deploymentOutcome"])
        self.assertEqual(ARTIFACT, p["attemptedArtifactId"])
        self.assertNotIn("artifactId", p)

    def test_cancelled_and_skipped_are_not_deployments(self):
        for status in ("cancelled", "skipped", "", "neutral"):
            self.assertIsNone(event(status), status)

    def test_the_event_carries_only_allowlisted_fields(self):
        for status in ("success", "failure"):
            e = event(status)
            self.assertLessEqual(set(e["data"]["customData"]), ALLOWED)
            self.assertEqual({"context", "subject", "customData"}, set(e["data"]))

    def test_an_attempt_keeps_its_identity_and_a_rerun_gets_a_new_one(self):
        self.assertEqual(event("success")["id"], event("success")["id"])
        self.assertNotEqual(event("success", "9001-1")["id"], event("success", "9001-2")["id"])

    def test_two_jobs_in_one_run_are_two_attempts(self):
        # Same run attempt, service, environment and revision (the review's probe).
        a, b = event("success", job="deploy-a"), event("failure", job="deploy-b")
        self.assertNotEqual(a["id"], b["id"])
        self.assertNotEqual(event("success", job="deploy-a")["id"],
                            event("success", job="deploy-b")["id"],
                            "two successful jobs must stay distinct too")

    def test_matrix_legs_are_distinct_and_stable(self):
        leg1, leg2 = '{"region":"eu"}', '{"region":"us"}'
        self.assertNotEqual(event("success", instance=leg1)["id"],
                            event("success", instance=leg2)["id"])
        self.assertEqual(event("success", instance=leg1)["id"],
                         event("success", instance=leg1)["id"])

    def test_bad_inputs_refuse(self):
        tf = emitter()
        when = datetime(2026, 10, 15, tzinfo=timezone.utc)
        for kwargs in ({"artifact": "not-a-purl"}, {"service": " "}, {"run_id": "has space"},
                       {"job": ""}, {"instance": " "}):
            args = dict(status="success", source="s", service="web", environment="prod",
                        artifact=ARTIFACT, run_id="r1", job="deploy", instance="i", when=when)
            args.update(kwargs)
            with self.assertRaises(ValueError):
                tf.build(**args)


class CliTests(unittest.TestCase):
    def run_cli(self, *args):
        env = {**os.environ, **GH_ENV}
        return subprocess.run([sys.executable, str(EMITTER), *args], env=env,
                              capture_output=True, text=True, timeout=60)

    def test_defaults_come_from_the_actions_environment(self):
        done = self.run_cli("--status", "success", "--service", "web", "--environment", "prod")
        self.assertEqual(0, done.returncode, done.stderr)
        e = json.loads(done.stdout)
        self.assertEqual(ARTIFACT, e["data"]["customData"]["attemptedArtifactId"])
        self.assertEqual("9001-2", e["data"]["customData"]["runId"])
        self.assertEqual("https://github.example/example/web/actions", e["source"])
        self.assertEqual("succeeded", props(e)["deploymentOutcome"])

    def test_without_an_instance_each_execution_is_distinct_and_replay_keeps_its_id(self):
        args = ("--status", "success", "--service", "web", "--environment", "prod")
        one, two = (json.loads(self.run_cli(*args).stdout) for _ in range(2))
        self.assertNotEqual(one["id"], two["id"], "unnamed matrix legs must not collide")
        self.assertEqual("deploy", one["data"]["customData"]["job"])
        # Replay: re-delivering the same printed event maps to the same record.
        first = ingest.episode_for(one, actor="t")["nodes"][0]["name"]
        again = ingest.episode_for(json.loads(json.dumps(one)), actor="t")["nodes"][0]["name"]
        self.assertEqual(first, again)

    def test_an_explicit_instance_is_stable_across_executions(self):
        args = ("--status", "success", "--service", "web", "--environment", "prod",
                "--instance", '{"region":"eu"}')
        self.assertEqual(json.loads(self.run_cli(*args).stdout)["id"],
                         json.loads(self.run_cli(*args).stdout)["id"])

    def test_a_cancelled_job_prints_nothing_and_succeeds(self):
        done = self.run_cli("--status", "cancelled", "--service", "web", "--environment", "prod")
        self.assertEqual((0, ""), (done.returncode, done.stdout))
        self.assertIn("no event", done.stderr)


class ActionTests(unittest.TestCase):
    """The composite step itself, run as bash with the inputs it maps."""

    def run_step(self, status, strict="false"):
        step = yaml.safe_load(ACTION.read_text())["runs"]["steps"][0]
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, **GH_ENV, "RUNNER_TEMP": tmp,
                   # A closed local port: the POST cannot succeed.
                   "QUIPU_SERVER": "http://127.0.0.1:9", "QUIPU_AUTH_TOKEN": "test-token",
                   "CD_STATUS": status, "CD_SERVICE": "web", "CD_ENVIRONMENT": "prod",
                   "CD_ARTIFACT": "", "CD_INSTANCE": "", "CD_STRICT": strict, "CAMAYOC_ROOT": str(ROOT)}
            return subprocess.run(["bash", "-c", step["run"]], env=env, capture_output=True,
                                  text=True, timeout=120)

    def test_an_unrecorded_event_warns_and_never_fails_the_deploy_by_default(self):
        done = self.run_step("success")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn("::warning::Camayoc deployment event not recorded", done.stdout)
        self.assertNotIn("test-token", done.stdout + done.stderr)

    def test_fail_on_error_makes_it_fail(self):
        self.assertEqual(1, self.run_step("success", strict="true").returncode)

    def test_a_cancelled_job_does_nothing(self):
        done = self.run_step("cancelled", strict="true")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertNotIn("warning", done.stdout)

    def test_the_action_declares_its_inputs(self):
        spec = yaml.safe_load(ACTION.read_text())
        self.assertEqual("composite", spec["runs"]["using"])
        for name in ("quipu-url", "quipu-token", "service", "environment"):
            self.assertTrue(spec["inputs"][name]["required"], name)


if __name__ == "__main__":
    unittest.main()
