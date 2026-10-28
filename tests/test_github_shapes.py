"""The GitHub shapes, against quipu's own SHACL engine (aegis-c01c.3).

The quipu CLI is found by tests/quipu_bin_guard.py: where QUIPU_BIN is promised
(the integration job) this suite RUNS, elsewhere it skips with the reason.
One positive control (the example data set) and one negative per constraint a
consumer depends on; every negative must fail with exactly ONE violation on the
path it names, so a syntax error or an unrelated rule cannot pass as a
negative.
"""
from __future__ import annotations

import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from quipu_bin_guard import QUIPU, requires_quipu

ROOT = Path(__file__).resolve().parents[1]
SHAPES = ROOT / "shapes" / "github.shapes.ttl"
EXAMPLE = ROOT / "tests" / "fixtures" / "github" / "example.ttl"
PREFIX = ("@prefix aegis: <http://aegis.gastown.local/ontology/> . "
          "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n")

CONTEXT = """
aegis:repo a aegis:GitHubRepository ; rdfs:label "repo" ;
  aegis:githubNodeId "R_kgDOExample1" ; aegis:githubFullName "example/widget" .
aegis:pr a aegis:GitHubPullRequest ; rdfs:label "pr" ; aegis:githubNodeId "PR_x" ;
  aegis:githubNumber 42 ; aegis:inGitHubRepository aegis:repo .
aegis:issue a aegis:GitHubIssue ; rdfs:label "issue" ; aegis:githubNodeId "I_x" ;
  aegis:githubNumber 9 ; aegis:inGitHubRepository aegis:repo .
"""

ACTIVITY = {
    "a": "aegis:GitHubActivity",
    "rdfs:label": '"act"',
    "aegis:sourceKind": '"observed"',
    "aegis:githubEventKind": '"pull_request.opened"',
    "aegis:githubActivityKey": '"' + "ab" * 32 + '"',
    "aegis:occurredAt": '"2026-10-20T14:00:00Z"',
    "aegis:aboutGitHubEntity": "aegis:pr",
    "aegis:inGitHubRepository": "aegis:repo",
    "aegis:githubActorLogin": '"alice"',
    "aegis:commitSha": '"' + "1" * 40 + '"',
    "aegis:githubState": '"open"',
}


def turtle(subject: str, fields: dict, **changes: str | None) -> str:
    fields = dict(fields)
    for key, value in changes.items():
        key = key.replace("__", ":")
        if value is None:
            fields.pop(key)
        else:
            fields[key] = value
    return f"{subject} " + " ;\n  ".join(f"{k} {v}" for k, v in fields.items()) + " .\n"


def activity(**changes: str | None) -> str:
    return CONTEXT + turtle("aegis:act", ACTIVITY, **changes)


@requires_quipu
class GitHubShapes(unittest.TestCase):
    def report(self, body: str, *, prefix: bool = True) -> str:
        with tempfile.NamedTemporaryFile("w", suffix=".ttl", delete=False) as fh:
            fh.write((PREFIX if prefix else "") + body)
        try:
            out = subprocess.run([str(QUIPU), "validate", "--shapes", str(SHAPES), "--data", fh.name],
                                 capture_output=True, text=True, check=False)
        finally:
            Path(fh.name).unlink()
        return out.stdout + out.stderr

    def valid(self, body: str) -> bool:
        return self.report(body).lstrip().startswith("valid")

    def assert_one_violation(self, body: str, constraint: str, path: str | None) -> None:
        out = self.report(body)
        self.assertTrue(out.lstrip().startswith("invalid: 1 violation"), out)
        self.assertIn(constraint, out)
        if path:
            self.assertRegex(out, rf"path: [^)]*{re.escape(path)}\)")

    def test_example_data_set_is_valid(self):
        out = self.report(EXAMPLE.read_text(), prefix=False)
        self.assertTrue(out.lstrip().startswith("valid"), out)

    def test_positive_controls_per_kind(self):
        self.assertTrue(self.valid(activity()))
        self.assertTrue(self.valid(activity(aegis__githubEventKind='"push"', aegis__githubState=None)))
        self.assertTrue(self.valid(activity(
            aegis__githubEventKind='"workflow_run.completed"', aegis__githubState=None,
            aegis__githubStatus='"completed"', aegis__githubConclusion='"failure"',
            aegis__githubRunAttempt="2")))
        self.assertTrue(self.valid(activity(
            aegis__githubEventKind='"pull_request_review.submitted"', aegis__githubState=None,
            aegis__reviewState='"changes_requested"')))
        self.assertTrue(self.valid(activity(
            aegis__githubState='"closed"', aegis__githubMerged="true",
            aegis__githubMergeCommitSha='"' + "2" * 40 + '"')))
        self.assertTrue(self.valid(activity(aegis__githubMentions='"bob", "carol-x"')))

    def test_activity_negative_controls(self):
        cases = {
            "unknown kind": (activity(aegis__githubEventKind='"star.created"'), "In constraint", "githubEventKind"),
            "no kind": (activity(aegis__githubEventKind=None), "MinCount(1)", "githubEventKind"),
            "key not a sha256": (activity(aegis__githubActivityKey='"abc"'), "Pattern(", "githubActivityKey"),
            "no time": (activity(aegis__occurredAt=None), "MinCount(1)", "occurredAt"),
            "time not RFC 3339": (activity(aegis__occurredAt='"2026-10-20 14:00"'), "Pattern(", "occurredAt"),
            "no subject": (activity(aegis__aboutGitHubEntity=None), "MinCount(1)", "aboutGitHubEntity"),
            "subject a literal": (activity(aegis__aboutGitHubEntity='"pr"'), "NodeKind", "aboutGitHubEntity"),
            "repo not a repository": (activity(aegis__inGitHubRepository="aegis:pr"), "Class", "inGitHubRepository"),
            "short sha": (activity(aegis__commitSha='"1111"'), "Pattern(", "commitSha"),
            "merged is not a state": (activity(aegis__githubState='"merged"'), "In constraint", "githubState"),
            "unknown conclusion": (activity(aegis__githubConclusion='"green"'), "In constraint", "githubConclusion"),
            "attempt zero": (activity(aegis__githubRunAttempt="0"), "MinInclusive", "githubRunAttempt"),
            "mention with @": (activity(aegis__githubMentions='"@bob"'), "Pattern(", "githubMentions"),
            "not observed": (activity(aegis__sourceKind='"inferred"'), "In constraint", "sourceKind"),
        }
        for name, (body, constraint, path) in cases.items():
            with self.subTest(name):
                self.assert_one_violation(body, constraint, path)

    def test_kind_dependent_state(self):
        cases = {
            "pull request without a state": activity(aegis__githubState=None),
            "issue without a state": activity(aegis__githubEventKind='"issues.closed"', aegis__githubState=None),
            "run without a status": activity(aegis__githubEventKind='"workflow_run.completed"', aegis__githubState=None),
            "check without a status": activity(aegis__githubEventKind='"check_run.completed"', aegis__githubState=None),
            "submitted review without a verdict": activity(
                aegis__githubEventKind='"pull_request_review.submitted"', aegis__githubState=None),
            "merged without a merge commit": activity(aegis__githubState='"closed"', aegis__githubMerged="true"),
        }
        for name, body in cases.items():
            with self.subTest(name):
                self.assert_one_violation(body, "OR not satisfied", None)

    def test_entity_negative_controls(self):
        repo = {"a": "aegis:GitHubRepository", "rdfs:label": '"r"', "aegis:githubNodeId": '"R_1"',
                "aegis:githubFullName": '"example/widget"'}
        pr = {"a": "aegis:GitHubPullRequest", "rdfs:label": '"p"', "aegis:githubNodeId": '"PR_1"',
              "aegis:githubNumber": "42", "aegis:inGitHubRepository": "aegis:r"}
        review = {"a": "aegis:GitHubReview", "rdfs:label": '"v"', "aegis:githubNodeId": '"PRR_1"',
                  "aegis:inGitHubRepository": "aegis:r", "aegis:onGitHubThread": "aegis:p"}
        run = {"a": "aegis:GitHubWorkflowRun", "rdfs:label": '"w"', "aegis:githubNodeId": '"WFR_1"',
               "aegis:inGitHubRepository": "aegis:r", "aegis:githubName": '"CI"',
               "aegis:commitSha": '"' + "1" * 40 + '"'}
        push = {"a": "aegis:GitHubPush", "rdfs:label": '"push"', "aegis:inGitHubRepository": "aegis:r",
                "aegis:githubRef": '"refs/heads/main"', "aegis:githubBeforeSha": '"' + "0" * 40 + '"',
                "aegis:githubAfterSha": '"' + "2" * 40 + '"'}
        issue = ('aegis:i a aegis:GitHubIssue ; rdfs:label "i" ; aegis:githubNodeId "I_1" ; '
                 'aegis:githubNumber 9 ; aegis:inGitHubRepository aegis:r .\n')

        def body(**changes):
            parts = {"repo": turtle("aegis:r", repo), "pr": turtle("aegis:p", pr),
                     "review": turtle("aegis:v", review), "run": turtle("aegis:w", run),
                     "push": turtle("aegis:push", push)}
            for name, fields_changes in changes.items():
                source = {"repo": repo, "pr": pr, "review": review, "run": run, "push": push}[name]
                subject = {"repo": "aegis:r", "pr": "aegis:p", "review": "aegis:v", "run": "aegis:w",
                           "push": "aegis:push"}[name]
                parts[name] = turtle(subject, source, **fields_changes)
            return issue + "".join(parts.values())

        self.assertTrue(self.valid(body()), self.report(body()))
        cases = {
            "repo without node id": (body(repo={"aegis:githubNodeId": None}), "MinCount(1)", "githubNodeId"),
            "repo name not owner/name": (body(repo={"aegis:githubFullName": '"widget"'}), "Pattern(", "githubFullName"),
            "pr without number": (body(pr={"aegis:githubNumber": None}), "MinCount(1)", "githubNumber"),
            "pr number zero": (body(pr={"aegis:githubNumber": "0"}), "MinInclusive", "githubNumber"),
            "pr in two repos": (body(pr={"aegis:inGitHubRepository": "aegis:r, aegis:r2"}) + turtle("aegis:r2", repo),
                                "MaxCount(1)", "inGitHubRepository"),
            "review on an issue": (body(review={"aegis:onGitHubThread": "aegis:i"}), "Class", "onGitHubThread"),
            "run without head": (body(run={"aegis:commitSha": None}), "MinCount(1)", "commitSha"),
            "push ref not a ref": (body(push={"aegis:githubRef": '"main"'}), "Pattern(", "githubRef"),
            "push without after": (body(push={"aegis:githubAfterSha": None}), "MinCount(1)", "githubAfterSha"),
        }
        for name, (data, constraint, path) in cases.items():
            with self.subTest(name):
                self.assert_one_violation(data, constraint, path)


if __name__ == "__main__":
    unittest.main()
