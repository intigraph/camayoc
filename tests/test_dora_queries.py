"""DORA stored queries on Quipu's own engine, through the real ingest (aegis-rb3).

Each change step and deployment is built as a CDEvent, mapped by
ingest_cdevents, loaded with `quipu episode` into a scratch store, and the
stored templates are run with `quipu read`. The joins must stay inside a
change's lifecycle: a green after the merge is not a pre-merge green, and a
deployment earlier than the merge never delivers it.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from quipu_bin_guard import QUIPU, requires_quipu

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("ingest_cdevents", ROOT / "scripts/ingest_cdevents.py")
ingest = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(ingest)

REPO = "example/example-svc"
WINDOW = {"repository": REPO, "start": "2026-10-14T00:00:00Z", "end": "2026-10-15T00:00:00Z"}


def sha(n: int) -> str:
    return f"{n:040x}"


def change(n: int, step: str, at: str, **custom) -> dict:
    kind = f"dev.cdevents.change.{step}.0.2.0"
    eid = f"{REPO}#{n}/{step}/{at}"
    data = {"context": {"version": "0.4.1", "id": eid, "source": "example-forge/changes", "type": kind, "timestamp": at},
            "subject": {"id": f"{REPO}#{n}", "source": "example-forge/changes", "type": "change",
                        "content": {"repository": {"id": REPO}}}}
    if custom:
        data["customData"] = custom
    return {"specversion": "1.0", "id": eid, "source": "example-forge/changes", "type": kind, "time": at, "data": data}


def green(n: int, at: str) -> dict:
    kind = "dev.cdevents.testsuiterun.finished.0.2.0"
    eid = f"{REPO}#{n}/ci/{sha(900 + n)}/pass/{at}"
    data = {"context": {"version": "0.4.1", "id": eid, "source": "example-forge/changes", "type": kind, "timestamp": at},
            "subject": {"id": f"{REPO}#{n}@{sha(900 + n)}", "type": "testSuiteRun",
                        "content": {"outcome": "pass", "environment": {"id": "ci"}}},
            "customData": {"changeId": f"{REPO}#{n}", "changeRepository": REPO, "commitSha": sha(900 + n)}}
    return {"specversion": "1.0", "id": eid, "source": "example-forge/changes", "type": kind, "time": at, "data": data}


def deployed(at: str, commits: list[str]) -> dict:
    kind = "dev.cdevents.service.deployed.0.2.0"
    eid = f"deploy-{at}"
    data = {"context": {"version": "0.4.1", "id": eid, "source": "example-cd", "type": kind, "timestamp": at},
            "subject": {"id": "example-svc", "type": "service",
                        "content": {"artifactId": f"pkg:github/{REPO}@{commits[0]}", "environment": {"id": "prod"}}},
            "customData": {"includedCommits": commits}}
    return {"specversion": "1.0", "id": eid, "source": "example-cd", "type": kind, "time": at, "data": data}


EVENTS = [
    # 1: chronological. Green before merge, deployed after merge.
    change(1, "created", "2026-10-14T10:00:00Z"), green(1, "2026-10-14T10:30:00Z"),
    change(1, "reviewed", "2026-10-14T11:00:00Z", reviewState="approved"),
    change(1, "merged", "2026-10-14T12:00:00Z", commitSha=sha(1)),
    green(1, "2026-10-14T12:20:00Z"),                       # a later green must not replace the first
    deployed("2026-10-14T12:30:00Z", [sha(1)]),
    # 2: its only green arrives AFTER the merge; a review after the merge too.
    change(2, "created", "2026-10-14T10:00:00Z"),
    change(2, "merged", "2026-10-14T12:00:00Z", commitSha=sha(2)),
    green(2, "2026-10-14T13:00:00Z"),
    change(2, "reviewed", "2026-10-14T13:30:00Z", reviewState="approved"),
    # A deployment BEFORE the merge that (wrongly) lists the merge commit.
    deployed("2026-10-14T11:00:00Z", [sha(2)]),
]


@requires_quipu
class DoraQueries(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = str(Path(cls.tmp.name) / "dora.db")
        for n, event in enumerate(EVENTS):
            path = Path(cls.tmp.name) / f"{n:02d}.json"
            path.write_text(json.dumps(ingest.episode_for(event, actor="tester")))
            subprocess.run([str(QUIPU), "episode", str(path), "--db", cls.db],
                           check=True, capture_output=True, text=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def rows(self, name: str) -> dict[str, list[str]]:
        template = json.loads((ROOT / "queries" / f"{name}.json").read_text())["template"]
        for key, value in WINDOW.items():
            template = template.replace("{" + key + "}", value)
        out = subprocess.run([str(QUIPU), "read", template, "--db", self.db],
                             check=True, capture_output=True, text=True).stdout
        lines = [line.split("\t") for line in out.splitlines()[2:] if "\t" in line]
        return {cells[0].strip('"'): [c.strip('"') for c in cells[1:]] for cells in lines}

    def test_first_green_is_inside_the_lifecycle(self):
        steps = self.rows("camayoc_dora_change_steps")
        self.assertEqual(["2026-10-14T10:00:00Z", "2026-10-14T10:30:00Z", "2026-10-14T12:00:00Z"],
                         steps[f"{REPO}#1"])
        # Negative: the only green is after the merge, so there was no pre-merge green.
        self.assertEqual("none", steps[f"{REPO}#2"][1])

    def test_reviews_after_the_merge_are_not_steps(self):
        reviews = self.rows("camayoc_dora_change_reviews")
        self.assertIn(f"{REPO}#1", reviews)
        self.assertNotIn(f"{REPO}#2", reviews)

    def test_delivery_needs_a_deployment_at_or_after_the_merge(self):
        deployed_rows = self.rows("camayoc_dora_change_deployed")
        self.assertEqual("2026-10-14T12:30:00Z", deployed_rows[f"{REPO}#1"][1])
        # Negative: the only deployment listing #2's merge commit predates the merge.
        self.assertEqual("none", deployed_rows[f"{REPO}#2"][1])


if __name__ == "__main__":
    unittest.main()
