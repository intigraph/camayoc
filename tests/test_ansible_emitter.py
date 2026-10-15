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

    def event(self, **recap):
        return plugin().build("web-1.example.invalid", recap, source="example-ci/ansible",
                              service="web", artifact=ARTIFACT, when=self.when)

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

    def test_the_same_apply_is_the_same_event(self):
        self.assertEqual(self.event(ok=1)["id"], self.event(ok=1)["id"])


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

    def run_play(self, *extra, env_drop=()):
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

    def test_a_check_run_is_not_a_deployment(self):
        _, events = self.run_play("--check")
        self.assertEqual([], events)

    def test_without_full_configuration_nothing_is_emitted(self):
        _, events = self.run_play(env_drop=("CAMAYOC_CDEVENTS_ARTIFACT",))
        self.assertEqual([], events)


if __name__ == "__main__":
    unittest.main()
