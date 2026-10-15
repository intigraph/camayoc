"""The Terraform CDEvents wrapper, end to end through Camayoc's ingest."""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "emitters" / "terraform" / "cdevents.py"
sys.path.insert(0, str(ROOT / "scripts"))
_spec = importlib.util.spec_from_file_location("ingest_cdevents", ROOT / "scripts/ingest_cdevents.py")
ingest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ingest)

ARTIFACT = "pkg:generic/example-iac@5e1f0c9d8b7a6f5e4d3c2b1a0f9e8d7c6b5a4f3e"


def wrapper():
    spec = importlib.util.spec_from_file_location("cdevents_tf", WRAPPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def line(kind: str, message: str = "", **fields) -> str:
    return json.dumps({"@level": "info", "@message": message or kind, "type": kind, **fields})


def resource(kind: str, action: str, addr: str = "example_thing.a") -> str:
    return line(kind, f"{addr}: {kind}", hook={"resource": {"addr": addr}, "action": action})


def summary(add=0, change=0, remove=0, operation="apply") -> str:
    return line("change_summary", "Apply complete!", changes={
        "add": add, "change": change, "import": 0, "remove": remove, "operation": operation})


ERROR = line("diagnostic", "Error: boom", diagnostic={"severity": "error", "summary": "boom"})
WARNING = line("diagnostic", "Warning: meh", diagnostic={"severity": "warning", "summary": "meh"})

SUCCESS = [line("version"), resource("apply_start", "create"),
           resource("apply_complete", "create"), summary(add=1)]
FAILURE = [line("version"), resource("apply_start", "update"),
           resource("apply_errored", "update"), ERROR]


def recap_of(lines, exit_code):
    tf = wrapper()
    recap = tf.Recap()
    for text in lines:
        recap.read(text)
    return recap, recap.counters(exit_code)


def event_of(lines, exit_code, run_id="run-a"):
    _, counters = recap_of(lines, exit_code)
    return wrapper().build(counters, source="example-ci/terraform", service="infra",
                           environment="prod", artifact=ARTIFACT, run_id=run_id,
                           when=datetime(2026, 10, 15, 7, 2, 11, tzinfo=timezone.utc))


def props(event):
    return ingest.episode_for(event, actor="t")["nodes"][0]["properties"]


class StreamTests(unittest.TestCase):
    def test_a_clean_apply_serves_what_it_applied(self):
        p = props(event_of(SUCCESS, 0))
        self.assertEqual("succeeded", p["deploymentOutcome"])
        self.assertEqual(ARTIFACT, p["artifactId"])
        self.assertEqual("prod", p["environment"])

    def test_an_errored_resource_is_a_failed_deployment_of_the_attempt(self):
        p = props(event_of(FAILURE, 1))
        self.assertEqual("failed", p["deploymentOutcome"])
        self.assertEqual(ARTIFACT, p["attemptedArtifactId"])
        self.assertNotIn("artifactId", p)

    def test_a_nonzero_exit_fails_even_when_the_stream_shows_no_error(self):
        # e.g. the process was killed after the last resource completed.
        self.assertEqual("failed", props(event_of(SUCCESS, 1))["deploymentOutcome"])

    def test_an_error_diagnostic_fails_even_with_exit_zero(self):
        self.assertEqual("failed", props(event_of(SUCCESS + [ERROR], 0))["deploymentOutcome"])

    def test_warnings_do_not_fail_an_apply(self):
        self.assertEqual("succeeded", props(event_of(SUCCESS + [WARNING], 0))["deploymentOutcome"])

    def test_the_summary_counts_and_the_resource_tally_agree_or_the_summary_wins(self):
        _, counters = recap_of(SUCCESS, 0)
        self.assertEqual({"add": 1, "change": 0, "import": 0, "remove": 0,
                          "errors": 0, "exitNonzero": 0}, counters)
        replace = [resource("apply_start", "replace"), resource("apply_complete", "replace")]
        _, counters = recap_of(replace, 1)
        self.assertEqual((1, 1, 1), (counters["add"], counters["remove"], counters["exitNonzero"]))

    def test_only_an_attempt_is_a_deployment(self):
        for name, lines in {
            "plan": [line("version"), summary(add=2, operation="plan")],
            "no-change apply": [line("version"), summary()],
            "failed before any resource": [line("version"), ERROR],
            "not json": ["Apply complete!"],
        }.items():
            with self.subTest(name):
                recap, _ = recap_of(lines, 1)
                self.assertFalse(recap.attempted())
        for lines in (SUCCESS, FAILURE, [summary(remove=1, operation="destroy")]):
            self.assertTrue(recap_of(lines, 0)[0].attempted())

    def test_the_counters_and_the_outcome_word_always_agree(self):
        # Camayoc refuses a Terraform event whose word contradicts its counters.
        for lines, code in ((SUCCESS, 0), (SUCCESS, 1), (FAILURE, 1), (FAILURE, 0)):
            with self.subTest(code=code, failing=lines is FAILURE):
                props(event_of(lines, code))
        event = event_of(SUCCESS, 0)
        event["data"]["subject"]["content"]["outcome"] = "failure"
        with self.assertRaises(ingest.Quarantined):
            ingest.episode_for(event, actor="t")
        event = event_of(SUCCESS, 0)
        del event["data"]["customData"]["recap"]["exitNonzero"]
        with self.assertRaises(ingest.Quarantined):
            ingest.episode_for(event, actor="t")

    def test_two_runs_stay_two_events_and_one_run_replays_to_one(self):
        a, b = event_of(SUCCESS, 0, "run-a"), event_of(SUCCESS, 0, "run-b")
        self.assertNotEqual(a["id"], b["id"])
        self.assertEqual(a["id"], event_of(SUCCESS, 0, "run-a")["id"])
        with self.assertRaises(ValueError):
            event_of(SUCCESS, 0, "bad id with spaces")


FAKE_TF = """#!/bin/sh
cat "$1"
exit "$2"
"""


class WrapperTests(unittest.TestCase):
    """The wrapper as a process: exit code, readable output, one spooled file."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.fake = self.dir / "terraform"
        self.fake.write_text(FAKE_TF)
        self.fake.chmod(self.fake.stat().st_mode | stat.S_IXUSR)
        self.spool = self.dir / "spool"
        self.env = {**os.environ,
                    "CAMAYOC_CDEVENTS_SPOOL": str(self.spool),
                    "CAMAYOC_CDEVENTS_SOURCE": "example-ci/terraform",
                    "CAMAYOC_CDEVENTS_SERVICE": "infra",
                    "CAMAYOC_CDEVENTS_ENVIRONMENT": "prod",
                    "CAMAYOC_CDEVENTS_ARTIFACT": ARTIFACT,
                    "CAMAYOC_CDEVENTS_RUN_ID": "ci-run-7"}

    def tearDown(self):
        self.tmp.cleanup()

    def run_wrapper(self, lines, code, *, json_flag=True, env=None):
        stream = self.dir / "stream.jsonl"
        stream.write_text("\n".join(lines) + "\n")
        argv = [sys.executable, str(WRAPPER), "--", str(self.fake), str(stream), str(code)]
        if json_flag:
            argv.append("-json")
        # The fake ignores its extra args; "-json" only tells the wrapper to parse.
        return subprocess.run(argv, env=env or self.env, capture_output=True, text=True,
                              timeout=60)

    def spooled(self):
        return sorted(self.spool.glob("*.json")) if self.spool.exists() else []

    def test_a_failed_apply_keeps_its_exit_code_and_spools_one_failed_event(self):
        done = self.run_wrapper(FAILURE, 1)
        self.assertEqual(1, done.returncode)
        self.assertIn("Error: boom", done.stdout)  # human messages, not raw JSON
        self.assertNotIn('"@level"', done.stdout)
        [path] = self.spooled()
        event = json.loads(path.read_text())
        self.assertEqual("failed", props(event)["deploymentOutcome"])
        self.assertEqual("ci-run-7", event["data"]["customData"]["runId"])

    def test_a_clean_apply_spools_one_succeeded_event(self):
        done = self.run_wrapper(SUCCESS, 0)
        self.assertEqual(0, done.returncode)
        [path] = self.spooled()
        self.assertEqual("succeeded", props(json.loads(path.read_text()))["deploymentOutcome"])

    def test_no_event_without_full_config_or_without_json(self):
        partial = {k: v for k, v in self.env.items() if k != "CAMAYOC_CDEVENTS_ENVIRONMENT"}
        self.assertEqual(0, self.run_wrapper(SUCCESS, 0, env=partial).returncode)
        done = self.run_wrapper(SUCCESS, 0, json_flag=False)
        self.assertEqual(0, done.returncode)
        self.assertIn('"@level"', done.stdout)  # passed through untouched
        self.assertEqual([], self.spooled())

    def test_an_unwritable_spool_is_a_warning_not_a_changed_result(self):
        self.spool.write_text("a file where the directory should be")
        done = self.run_wrapper(SUCCESS, 0)
        self.assertEqual(0, done.returncode)
        self.assertIn("event not spooled", done.stderr)


if __name__ == "__main__":
    unittest.main()
