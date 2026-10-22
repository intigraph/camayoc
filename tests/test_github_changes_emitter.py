"""The GitHub change emitter states lifecycle steps; Camayoc maps every one (aegis-rb3)."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
import unittest.mock
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


def run(name, conclusion, at, *, started=None, rid=0):
    """A completed check run; started defaults to its completion."""
    return {"__typename": "CheckRun", "databaseId": rid, "name": name, "status": "COMPLETED",
            "conclusion": conclusion, "startedAt": started or at, "completedAt": at}


def pending(name, started, *, rid=0):
    return {"__typename": "CheckRun", "databaseId": rid, "name": name, "status": "IN_PROGRESS",
            "conclusion": None, "startedAt": started, "completedAt": None}


def queued(name, *, rid=0):
    """GitHub's nullable fields on a queued run: no start, no completion."""
    return {"__typename": "CheckRun", "databaseId": rid, "name": name, "status": "QUEUED",
            "conclusion": None, "startedAt": None, "completedAt": None}


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


    def test_a_rerun_in_progress_does_not_inherit_an_earlier_green(self):
        older = [run("Build", "SUCCESS", "2026-10-14T10:20:00Z", rid=1), run("Test", "SUCCESS", "2026-10-14T10:30:00Z")]
        self.assertIsNone(emitter.ci_result(older + [pending("Build", "2026-10-14T11:00:00Z", rid=2)], REQUIRED))
        # Control: the same rerun, completed, decides.
        done = run("Build", "SUCCESS", "2026-10-14T11:10:00Z", started="2026-10-14T11:00:00Z", rid=2)
        self.assertEqual(("pass", "2026-10-14T11:10:00Z"), emitter.ci_result(older + [done], REQUIRED))

    def test_a_queued_rerun_with_null_times_is_undecided_in_any_order(self):
        older = [run("Build", "SUCCESS", "2026-10-14T10:20:00Z", rid=5), run("Test", "SUCCESS", "2026-10-14T10:30:00Z")]
        for rid in (1, 9):   # a lower or higher run id than the dated success
            with self.subTest(rid=rid):
                self.assertIsNone(emitter.ci_result(older + [queued("Build", rid=rid)], REQUIRED))
                self.assertIsNone(emitter.ci_result([queued("Build", rid=rid)] + older, REQUIRED))
        # Control: once that rerun completes, it decides.
        done = run("Build", "SUCCESS", "2026-10-14T11:10:00Z", started="2026-10-14T11:00:00Z", rid=9)
        self.assertEqual(("pass", "2026-10-14T11:10:00Z"), emitter.ci_result(older + [done], REQUIRED))


def with_ci(nodes):
    return pr(commits={"nodes": [{"commit": {"oid": HEAD, "statusCheckRollup": {"contexts": {"nodes": nodes}}}}]})


class RetriesOnOneCommit(unittest.TestCase):
    """Each observed CI result is its own event; a re-observation is not."""

    def poll(self, spool_dir, nodes):
        spooled = [emitter.spool(e, spool_dir) for e in emitter.events_for("example/example-svc", with_ci(nodes), REQUIRED)]
        return [p.name for p in spooled if p and "_ci_" in p.name]

    def deliver_all(self, spool_dir):
        (spool_dir / "delivered").mkdir(exist_ok=True)
        for p in spool_dir.glob("*.json"):
            p.rename(spool_dir / "delivered" / p.name)

    def test_fail_then_pass_then_fail_each_reach_the_spool(self):
        red = [run("Build", "FAILURE", "2026-10-14T10:20:00Z", rid=1), run("Test", "SUCCESS", "2026-10-14T10:30:00Z")]
        green = red[1:] + [run("Build", "SUCCESS", "2026-10-14T11:10:00Z", started="2026-10-14T11:00:00Z", rid=2)]
        red_again = green[:1] + [run("Build", "FAILURE", "2026-10-14T12:10:00Z", started="2026-10-14T12:00:00Z", rid=3)]
        with tempfile.TemporaryDirectory() as tmp:
            spool_dir = Path(tmp)
            self.assertEqual(1, len(self.poll(spool_dir, red)))
            self.deliver_all(spool_dir)
            self.assertEqual([], self.poll(spool_dir, red))          # same result, already delivered
            got = self.poll(spool_dir, green)
            self.assertEqual(1, len(got))
            event = json.loads((spool_dir / got[0]).read_text())
            self.assertEqual("ci-passed", ingest.episode_for(event, actor="t")["nodes"][0]["properties"]["changeAction"])
            self.deliver_all(spool_dir)
            later = self.poll(spool_dir, red_again)
            self.assertEqual(1, len(later))
            self.assertIn("fail", later[0])
            self.assertEqual(3, len(list((spool_dir / "delivered").glob("*_ci_*"))) + len(later))


class Boundary(unittest.TestCase):
    def test_not_before_drops_earlier_steps_only(self):
        with tempfile.TemporaryDirectory() as tmp, \
                unittest.mock.patch.object(emitter, "required_checks", return_value=REQUIRED), \
                unittest.mock.patch.object(emitter, "pull_requests", return_value=[pr()]), \
                unittest.mock.patch("sys.stdout"):
            rc = emitter.main(["--repo", "example/example-svc", "--since", "2026-10-14T00:00:00Z",
                               "--spool", tmp, "--not-before", "2026-10-14T11:00:00Z"])
            names = sorted(p.name for p in Path(tmp).glob("*.json"))
        self.assertEqual(0, rc)
        # created (10:00) and CI (10:30) predate the boundary; review (12:00) and merge (18:00) do not.
        self.assertEqual(2, len(names))
        self.assertTrue(any("_merged" in n for n in names) and any("_review_" in n for n in names))


class BoundaryParsing(unittest.TestCase):
    def run_main(self, not_before):
        with tempfile.TemporaryDirectory() as tmp, \
                unittest.mock.patch.object(emitter, "required_checks", return_value=REQUIRED), \
                unittest.mock.patch.object(emitter, "pull_requests", return_value=[pr()]) as fetch, \
                unittest.mock.patch("sys.stdout"), unittest.mock.patch("sys.stderr"):
            try:
                rc = emitter.main(["--repo", "example/example-svc", "--since", "2026-10-14T00:00:00Z",
                                   "--spool", tmp, "--not-before", not_before])
            except SystemExit as exit_:
                rc = exit_.code
            return rc, sorted(p.name for p in Path(tmp).glob("*.json")), fetch.called

    def test_fractional_boundary_is_compared_as_an_instant(self):
        # review at 12:00:00Z is BEFORE 12:00:00.500Z, though it sorts after it as text.
        rc, names, _ = self.run_main("2026-10-14T12:00:00.500Z")
        self.assertEqual(0, rc)
        self.assertEqual(1, len(names))
        self.assertTrue(names[0].endswith("_merged.json"))

    def test_the_exact_instant_is_included(self):
        rc, names, _ = self.run_main("2026-10-14T12:00:00Z")
        self.assertEqual((0, 2), (rc, len(names)))

    def test_invalid_boundary_is_refused_before_any_forge_call(self):
        for bad in ("yesterday", "2026-10-14T12:00:00"):
            with self.subTest(bad):
                rc, names, fetched = self.run_main(bad)
                self.assertEqual(2, rc)
                self.assertEqual([], names)
                self.assertFalse(fetched)


def review_status(state, description, created="2026-10-14T23:59:00Z"):
    return {"__typename": "StatusContext", "context": "review/approved", "state": state,
            "createdAt": created, "description": description}


class NativeReviewStatus(unittest.TestCase):
    """aegis-6ye: the native receipt's reviewed_at, never the status publication time."""

    GOOD = "reviewer wu receipt r-0123456789abcdef reviewed_at 2026-10-14T11:15:30.123456789Z"

    def nodes(self, *extra):
        return [run("Build", "SUCCESS", "2026-10-14T10:20:00Z"), run("Test", "SUCCESS", "2026-10-14T10:30:00Z"), *extra]

    def test_a_verified_approve_is_a_review_at_reviewed_at(self):
        events = emitter.events_for("example/example-svc", with_ci(self.nodes(review_status("SUCCESS", self.GOOD))), REQUIRED)
        native = [e for e in events if "/review/native-" in e["id"]]
        self.assertEqual(1, len(native))
        self.assertEqual("2026-10-14T11:15:30.123456789Z", native[0]["time"])   # not createdAt, precision kept
        props = ingest.episode_for(native[0], actor="t")["nodes"][0]["properties"]
        self.assertEqual(("reviewed", "approved", HEAD), (props["changeAction"], props["reviewState"], props["commitSha"]))

    def test_revocation_legacy_and_offset_times_record_nothing(self):
        for name, ctx in (("revoked", review_status("FAILURE", self.GOOD)),
                          ("legacy", review_status("SUCCESS", "reviewer wu receipt r-0123")),
                          ("offset", review_status("SUCCESS", "reviewer wu receipt r-1 reviewed_at 2026-10-14T11:15:30+02:00")),
                          ("garbage", review_status("SUCCESS", "reviewer wu receipt r-1 reviewed_at yesterday"))):
            with self.subTest(name):
                events = emitter.events_for("example/example-svc", with_ci(self.nodes(ctx)), REQUIRED)
                self.assertEqual([], [e for e in events if "/review/native-" in e["id"]])

    def test_a_required_review_status_never_delays_green(self):
        required = REQUIRED + ["review/approved"]
        self.assertEqual(("pass", "2026-10-14T10:30:00Z"), emitter.ci_result(self.nodes(), required))
        pending_review = self.nodes(review_status("PENDING", "", created="2026-10-14T12:00:00Z"))
        self.assertEqual(("pass", "2026-10-14T10:30:00Z"), emitter.ci_result(pending_review, required))


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
        self.assertEqual(["created", f"ci/{HEAD}/pass/2026-10-14T10:30:00Z", "review/comment-7", "merged"], steps)
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
