"""The GitHub change emitter states lifecycle steps; Camayoc maps every one (aegis-rb3)."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


emitter = load("github_changes", ROOT / "emitters/github/changes.py")
ingest = load("ingest_cdevents", ROOT / "scripts/ingest_cdevents.py")

REQUIRED = ["Build", "Test"]
HEAD = "9f1c2e3d4b5a69788796a5b4c3d2e1f0a9b8c7d6"
MERGE = "3392682256648f3ec088dd768b270a0097089a7a"


def run(name, conclusion, at):
    return {"__typename": "CheckRun", "name": name, "conclusion": conclusion, "completedAt": at}


def pr(**over):
    base = {
        "number": 42, "createdAt": "2026-10-14T10:00:00Z", "updatedAt": "2026-10-14T18:00:00Z",
        "closedAt": "2026-10-14T18:00:00Z", "mergedAt": "2026-10-14T18:00:00Z", "state": "MERGED",
        "mergeCommit": {"oid": MERGE},
        "reviews": {"nodes": []},
        "comments": {"nodes": [{"fullDatabaseId": "7", "createdAt": "2026-10-14T12:00:00Z",
                                "body": "SOURCE APPROVE at exact 9f1c2e3d (independent review)"}]},
        "commits": {"nodes": [{"commit": {"oid": HEAD, "statusCheckRollup": {"contexts": {"nodes": [
            run("Build", "SUCCESS", "2026-10-14T10:20:00Z"), run("Test", "SUCCESS", "2026-10-14T10:30:00Z"),
            run("Docs", "FAILURE", "2026-10-14T10:05:00Z")]}}}}]},
    }
    base.update(over)
    return base


class GreenIsTheRequiredChecks(unittest.TestCase):
    def test_all_required_passing_is_green_at_the_last_one(self):
        nodes = [run("Build", "SUCCESS", "2026-10-14T10:20:00Z"), run("Test", "SKIPPED", "2026-10-14T10:30:00Z"),
                 run("Docs", "FAILURE", "2026-10-14T10:40:00Z")]
        self.assertEqual(("pass", "2026-10-14T10:30:00Z"), emitter.ci_result(nodes, REQUIRED))

    def test_a_partial_run_is_not_green(self):
        # The rollup of a path-filtered push can be SUCCESS with two checks.
        self.assertIsNone(emitter.ci_result([run("Build", "SUCCESS", "2026-10-14T10:20:00Z")], REQUIRED))

    def test_a_failed_required_check_is_red_at_its_completion(self):
        nodes = [run("Build", "FAILURE", "2026-10-14T10:20:00Z"), run("Test", "SUCCESS", "2026-10-14T10:30:00Z")]
        self.assertEqual(("fail", "2026-10-14T10:20:00Z"), emitter.ci_result(nodes, REQUIRED))

    def test_a_rerun_supersedes_and_cancelled_is_undecided(self):
        nodes = [run("Build", "FAILURE", "2026-10-14T10:20:00Z"), run("Build", "SUCCESS", "2026-10-14T11:00:00Z"),
                 run("Test", "SUCCESS", "2026-10-14T10:30:00Z")]
        self.assertEqual(("pass", "2026-10-14T11:00:00Z"), emitter.ci_result(nodes, REQUIRED))
        cancelled = [run("Build", "CANCELLED", "2026-10-14T10:20:00Z"), run("Test", "SUCCESS", "2026-10-14T10:30:00Z")]
        self.assertIsNone(emitter.ci_result(cancelled, REQUIRED))


class ReviewVerdicts(unittest.TestCase):
    def test_verdict_words(self):
        cases = {
            "SOURCE APPROVE at exact 909089d2 (independent reviewer)": "approved",
            "Ian independent post-green source review: **CHANGES** at `9f1c`": "changes_requested",
            "Changes requested — exact `9f1c`.": "changes_requested",
            "**Independent review by malcolm**\n\nVerdict: APPROVE": "approved",
            "Superseded by #729, which carries this branch's commit": None,
            "Merged current main into the branch (no CHANGES to behaviour)": None,
            "approve later maybe": None,
        }
        for body, expected in cases.items():
            with self.subTest(body):
                self.assertEqual(expected, emitter.verdict(body))


class Events(unittest.TestCase):
    def test_lifecycle_and_every_event_maps(self):
        events = emitter.events_for("example/example-svc", pr(), REQUIRED)
        steps = [e["id"].split("/", 2)[2] for e in events]
        self.assertEqual(["created", f"ci/{HEAD}", "review/comment-7", "merged"], steps)
        actions = [ingest.episode_for(e, actor="t")["nodes"][0]["properties"]["changeAction"] for e in events]
        self.assertEqual(["created", "ci-passed", "reviewed", "merged"], actions)
        merged = ingest.episode_for(events[-1], actor="t")["nodes"][0]["properties"]
        self.assertEqual(MERGE, merged["commitSha"])

    def test_closed_unmerged_is_abandoned(self):
        events = emitter.events_for("example/example-svc", pr(mergedAt=None, mergeCommit=None, state="CLOSED"), REQUIRED)
        self.assertEqual("abandoned", events[-1]["id"].rsplit("/", 1)[1])

    def test_a_rerun_spools_nothing_new(self):
        with tempfile.TemporaryDirectory() as tmp:
            events = emitter.events_for("example/example-svc", pr(), REQUIRED)
            first = [emitter.spool(e, Path(tmp)) for e in events]
            again = [emitter.spool(e, Path(tmp)) for e in events]
            self.assertTrue(all(first))
            self.assertEqual([None] * len(events), again)
            self.assertEqual(events[0], json.loads(first[0].read_text()))

    def test_pages_stop_at_since(self):
        pages = iter([{"data": {"repository": {"pullRequests": {
            "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
            "nodes": [pr(number=2, updatedAt="2026-10-15T00:00:00Z"), pr(number=1, updatedAt="2026-10-13T00:00:00Z")]}}}}])
        got = [p["number"] for p in emitter.pull_requests("o/r", "2026-10-14T00:00:00Z", fetch=lambda _v: next(pages))]
        self.assertEqual([2], got)


if __name__ == "__main__":
    unittest.main()
