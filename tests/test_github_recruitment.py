"""Crash, duplicate-delivery and authority-change controls; no real spawn."""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from github_recruitment import Attempts, Candidate, attempt


class RecruitmentTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.addCleanup(self.conn.close)
        self.ledger = Attempts(self.conn)
        self.candidate = Candidate("intigraph/widget", 42, "a" * 40, "review", "task-1")
        self.spawns = []

    def run_attempt(self, candidate=None, **changes):
        options = dict(delivery="verified-delivery", event_at=1.,
                       admit=lambda _: None, spawn=lambda c: self.spawns.append(c) or "job-receipt",
                       clock=lambda: 2.)
        options.update(changes)
        return attempt(self.ledger, candidate or self.candidate, **options)

    def test_duplicate_event_and_new_delivery_cannot_spawn_twice(self):
        self.assertEqual("spawned", self.run_attempt())
        self.assertEqual("duplicate", self.run_attempt(delivery="other-delivery"))
        self.assertEqual(1, len(self.spawns))
        row = self.conn.execute("SELECT state,receipt,event_at,attempted_at FROM recruitment_attempts").fetchone()
        self.assertEqual(("spawned", "job-receipt", 1., 2.), row)

    def test_lost_response_and_refusal_are_terminal(self):
        def lost(_):
            self.spawns.append("delivered")
            raise TimeoutError("secret transport output must not be stored")
        self.assertEqual("unknown", self.run_attempt(spawn=lost))
        self.assertEqual("duplicate", self.run_attempt())
        self.assertEqual(["delivered"], self.spawns)
        self.assertEqual("TimeoutError", self.conn.execute("SELECT reason FROM recruitment_attempts").fetchone()[0])
        other = Candidate("intigraph/widget", 42, "b" * 40, "review", "task-1")
        self.assertEqual("refused", self.run_attempt(other, admit=lambda _: "head changed"))
        self.assertEqual("duplicate", self.run_attempt(other))
        self.assertEqual(["delivered"], self.spawns)

    def test_crashed_process_keeps_reservation_across_connections(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "spool.sqlite"
            first = sqlite3.connect(path, isolation_level=None)
            self.assertTrue(Attempts(first).reserve(self.candidate, "event-1", 1., 2.))
            first.close()
            second = sqlite3.connect(path, isolation_level=None)
            self.addCleanup(second.close)
            ledger = Attempts(second)
            self.assertFalse(ledger.reserve(self.candidate, "event-2", 1., 3.))
            self.assertEqual("attempted", second.execute("SELECT state FROM recruitment_attempts").fetchone()[0])

    def test_unconfirmed_receipt_does_not_count_as_spawned(self):
        self.assertEqual("unknown", self.run_attempt(spawn=lambda _: None))
        self.assertEqual("duplicate", self.run_attempt())
        self.assertFalse(self.spawns)

    def test_merge_key_is_separate_but_requires_explicit_admission(self):
        self.assertEqual("spawned", self.run_attempt())
        merge = Candidate("intigraph/widget", 42, "a" * 40, "merge", "task-1")
        self.assertEqual("refused", self.run_attempt(merge, admit=lambda _: "signing held"))
        self.assertEqual(1, len(self.spawns))

    def test_invalid_candidate_never_calls_authority_or_transport(self):
        for repo, pr, head, job, item in [
            ("outside/widget", 1, "a" * 40, "review", "task-1"),
            ("intigraph/..", 1, "a" * 40, "review", "task-1"),
            ("intigraph/widget", True, "a" * 40, "review", "task-1"),
            ("intigraph/widget", 1, "short", "review", "task-1"),
            ("intigraph/widget", 1, "a" * 40, "task", "task-1"),
            ("intigraph/widget", 1, "a" * 40, "review", "--unsafe"),
        ]:
            with self.subTest(repo=repo, pr=pr, head=head, job=job, item=item), self.assertRaises(ValueError):
                self.run_attempt(Candidate(repo, pr, head, job, item))
        self.assertFalse(self.spawns)


if __name__ == "__main__":
    unittest.main()
