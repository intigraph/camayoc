"""The Ansible CDEvents callback, end to end through Camayoc's ingest."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / "emitters" / "ansible"
sys.path.insert(0, str(ROOT / "scripts"))
_spec = importlib.util.spec_from_file_location("ingest_cdevents", ROOT / "scripts/ingest_cdevents.py")
ingest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ingest)

ARTIFACT = "pkg:generic/example-iac@5e1f0c9d8b7a6f5e4d3c2b1a0f9e8d7c6b5a4f3e"
HAVE_ANSIBLE = shutil.which("ansible-playbook") is not None


def plugin():
    spec = importlib.util.spec_from_file_location("cdevents_cb", PLUGIN_DIR / "cdevents.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BuildTests(unittest.TestCase):
    when = datetime(2026, 10, 15, 7, 2, 11, tzinfo=timezone.utc)

    def event(self, run_id="run-a", **recap):
        return plugin().build("web-1.example.invalid", recap, source="example-ci/ansible",
                              service="web", artifact=ARTIFACT, when=self.when, run_id=run_id)

    def test_a_failed_host_is_a_failed_deployment_of_the_attempt(self):
        props = ingest.episode_for(self.event(ok=5, failures=1), actor="t")["nodes"][0]["properties"]
        self.assertEqual("failed", props["deploymentOutcome"])
        self.assertEqual(ARTIFACT, props["attemptedArtifactId"])
        self.assertNotIn("artifactId", props)

    def test_unreachable_counts_as_failed(self):
        props = ingest.episode_for(self.event(ok=0, unreachable=1), actor="t")["nodes"][0]["properties"]
        self.assertEqual("failed", props["deploymentOutcome"])

    def test_a_clean_host_serves_what_it_applied(self):
        props = ingest.episode_for(self.event(ok=7, changed=2), actor="t")["nodes"][0]["properties"]
        self.assertEqual("succeeded", props["deploymentOutcome"])
        self.assertEqual(ARTIFACT, props["artifactId"])
        self.assertEqual("web-1.example.invalid", props["environment"])

    def test_the_recap_and_the_outcome_word_always_agree(self):
        # Camayoc refuses an Ansible event whose word contradicts its recap.
        for recap in ({"ok": 1}, {"ok": 1, "failures": 1}, {"ok": 0, "unreachable": 1}):
            with self.subTest(recap):
                event = self.event(**recap)
                self.assertEqual("ansible", event["data"]["customData"]["emitter"])
                ingest.episode_for(event, actor="t")

    def test_the_same_apply_is_the_same_event(self):
        self.assertEqual(self.event(ok=1)["id"], self.event(ok=1)["id"])

    # dearing's review of #125: same-second applies must not collapse.
    def test_two_applies_in_the_same_second_stay_two_events(self):
        spool_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, spool_dir)
        m = plugin()
        failed = self.event(run_id="run-a", ok=5, failures=1)
        fixed = self.event(run_id="run-b", ok=6)
        self.assertNotEqual(failed["id"], fixed["id"])
        paths = {m.spool(failed, spool_dir), m.spool(fixed, spool_dir)}
        self.assertEqual(2, len(paths))
        self.assertEqual(2, len(os.listdir(spool_dir)))
        outcomes = sorted(ingest.episode_for(json.loads(Path(p).read_text()), actor="t")["nodes"][0]
                          ["properties"]["deploymentOutcome"] for p in paths)
        self.assertEqual(["failed", "succeeded"], outcomes)

    def test_replaying_one_run_never_makes_a_third_event(self):
        spool_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, spool_dir)
        m = plugin()
        first = m.spool(self.event(run_id="run-a", ok=5, failures=1), spool_dir)
        second = m.spool(self.event(run_id="run-b", ok=6), spool_dir)
        again = m.spool(self.event(run_id="run-a", ok=5, failures=1), spool_dir)
        self.assertEqual(first, again)
        self.assertEqual(sorted([first, second]), sorted(str(p) for p in Path(spool_dir).glob("*.json")))
        body = json.loads(Path(first).read_text())
        name = ingest.episode_for(body, actor="t")["nodes"][0]["name"]
        self.assertEqual(name, ingest.episode_for(body, actor="t")["nodes"][0]["name"])

    def test_a_different_event_never_overwrites_a_spooled_one(self):
        spool_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, spool_dir)
        m = plugin()
        event = self.event(run_id="run-a", ok=5, failures=1)
        path = m.spool(event, spool_dir)
        forged = json.loads(json.dumps(event))
        forged["data"]["customData"]["recap"]["failures"] = 0
        with self.assertRaises(FileExistsError):
            m.spool(forged, spool_dir)
        self.assertEqual(1, json.loads(Path(path).read_text())["data"]["customData"]["recap"]["failures"])

    def test_run_id_is_required_and_bounded(self):
        for bad in ("", "has space", "x" * 129, None):
            with self.subTest(bad):
                with self.assertRaises(ValueError):
                    self.event(run_id=bad, ok=1)


@unittest.skipUnless(HAVE_ANSIBLE, "ansible not installed")
class RealPlaybookTests(unittest.TestCase):
    PLAY = """
- hosts: all
  gather_facts: false
  tasks:
    - name: works everywhere
      ansible.builtin.command: "true"
      changed_when: false
    - name: fails on the bad host only
      ansible.builtin.command: "false"
      when: inventory_hostname == 'bad'
"""

    def run_play(self, *extra, env_drop=(), tmp=None, env_add=None):
        if tmp is None:
            tmp = Path(tempfile.mkdtemp())
            self.addCleanup(shutil.rmtree, tmp)
        (tmp / "play.yml").write_text(self.PLAY)
        (tmp / "hosts").write_text("good ansible_connection=local\nbad ansible_connection=local\n")
        env = dict(os.environ, ANSIBLE_CALLBACKS_ENABLED="cdevents",
                   ANSIBLE_CALLBACK_PLUGINS=str(PLUGIN_DIR), ANSIBLE_NOCOLOR="1",
                   ANSIBLE_PYTHON_INTERPRETER=sys.executable, HOME=str(tmp),
                   ANSIBLE_LOCAL_TEMP=str(tmp / "ansible-local"),
                   ANSIBLE_REMOTE_TEMP=str(tmp / "ansible-remote"),
                   CAMAYOC_CDEVENTS_SPOOL=str(tmp / "spool"),
                   CAMAYOC_CDEVENTS_SOURCE="example-ci/ansible",
                   CAMAYOC_CDEVENTS_SERVICE="web", CAMAYOC_CDEVENTS_ARTIFACT=ARTIFACT)
        for key in env_drop:
            env.pop(key)
        env.update(env_add or {})
        run = subprocess.run(["ansible-playbook", "-i", str(tmp / "hosts"), str(tmp / "play.yml"), *extra],
                             env=env, capture_output=True, text=True, timeout=120)
        events = [json.loads(p.read_text()) for p in sorted((tmp / "spool").glob("*.json"))] \
            if (tmp / "spool").is_dir() else []
        return run, events

    def test_each_host_gets_its_own_outcome_from_the_recap(self):
        run, events = self.run_play()
        self.assertNotEqual(0, run.returncode, "the play itself fails on the bad host")
        outcomes = {}
        for event in events:
            props = ingest.episode_for(event, actor="t")["nodes"][0]["properties"]
            outcomes[props["environment"]] = props["deploymentOutcome"]
        self.assertEqual({"good": "succeeded", "bad": "failed"}, outcomes)

    def test_two_real_runs_keep_both_runs_events(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        self.run_play(tmp=tmp)
        _, events = self.run_play(tmp=tmp)
        self.assertEqual(4, len(events), "2 hosts x 2 runs")
        self.assertEqual(2, len({e["data"]["customData"]["runId"] for e in events}))

    def test_an_explicit_run_id_replayed_adds_nothing(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        _, first = self.run_play(tmp=tmp, env_add={"CAMAYOC_CDEVENTS_RUN_ID": "ci-run-42"})
        self.assertEqual({"ci-run-42"}, {e["data"]["customData"]["runId"] for e in first})
        self.assertEqual(2, len(first))

    def test_a_check_run_is_not_a_deployment(self):
        _, events = self.run_play("--check")
        self.assertEqual([], events)

    def test_without_full_configuration_nothing_is_emitted(self):
        _, events = self.run_play(env_drop=("CAMAYOC_CDEVENTS_ARTIFACT",))
        self.assertEqual([], events)


if __name__ == "__main__":
    unittest.main()
