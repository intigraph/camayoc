"""Synthetic discussion payloads; no claim of recorded live delivery."""
import copy
import json
import unittest
import subprocess
import tempfile
from pathlib import Path
from quipu_bin_guard import QUIPU, requires_quipu
from test_github_ingress import ROOT, GRAPH
from test_github_ingress import gi, activity


class DiscussionTests(unittest.TestCase):
    def payload(self, action="created"):
        return {"action": action, "repository": {"node_id": "R_test", "full_name": "example/project"},
                "sender": {"login": "outside-user"},
                "discussion": {"node_id": "D_test", "number": 12,
                               "created_at": "2026-11-21T10:00:00Z",
                               "updated_at": "2026-11-21T11:00:00Z",
                               "user": {"login": "thread-author"},
                               "body": "private body @mentioned"},
                "comment": {"node_id": "DC_test", "created_at": "2026-11-21T10:30:00Z",
                            "updated_at": "2026-11-21T11:30:00Z",
                            "user": {"login": "comment-author"},
                            "body": "never execute $(command) @mentioned"}}

    def build(self, event, payload):
        return gi.episode_for(event, payload, actor="tester")

    def test_discussion_is_a_distinct_thread_not_issue(self):
        episode, _ = self.build("discussion", self.payload())
        discussion = next(n for n in episode["nodes"] if n["type"] == "GitHubDiscussion")
        self.assertEqual(discussion["properties"]["githubNumber"], 12)
        self.assertEqual(discussion["properties"]["githubAuthorLogin"], "thread-author")
        self.assertNotIn("GitHubIssue", [n["type"] for n in episode["nodes"]])
        self.assertEqual(activity(episode)["githubActorLogin"], "outside-user")

    def test_comment_points_to_discussion_with_sender_distinct_from_author(self):
        episode, _ = self.build("discussion_comment", self.payload())
        self.assertIn({"source": "github.DC_test", "target": "github.D_test", "relation": "onGitHubThread"}, episode["edges"])
        self.assertEqual(activity(episode)["occurredAt"], "2026-11-21T10:30:00Z")
        self.assertNotIn("never execute", json.dumps(episode))

    def test_redaction_preserves_mapping_and_discussion_identity(self):
        for event in ["discussion", "discussion_comment"]:
            for action in ["created", "edited"]:
                with self.subTest(event=event, action=action):
                    payload = self.payload(action)
                    redacted = gi.redact(payload)
                    self.assertNotIn("private body", json.dumps(redacted))
                    self.assertEqual(self.build(event, payload), self.build(event, redacted))

    def test_edit_uses_its_source_time_and_distinct_activity_key(self):
        for event in ["discussion", "discussion_comment"]:
            payload = self.payload("edited")
            first, key = self.build(event, payload)
            field = "discussion" if event == "discussion" else "comment"
            payload[field]["updated_at"] = "2026-11-21T12:00:00Z"
            second, changed = self.build(event, payload)
            self.assertNotEqual(key, changed)
            self.assertNotEqual(activity(first)["occurredAt"], activity(second)["occurredAt"])
            self.assertEqual((second, changed), self.build(event, copy.deepcopy(payload)))

    def test_unknown_actions_and_missing_identity_or_time_refuse(self):
        for event in ["discussion", "discussion_comment"]:
            for action in ["deleted", "answered", "invented"]:
                with self.subTest(event=event, action=action):
                    with self.assertRaises(gi.Quarantined): self.build(event, self.payload(action))
            for field, key in [("discussion", "node_id"), ("discussion", "number"),
                               ("discussion" if event == "discussion" else "comment", "created_at")]:
                with self.subTest(event=event, missing=key):
                    payload = self.payload(); del payload[field][key]
                    with self.assertRaises(gi.Quarantined): self.build(event, payload)

    def test_spool_redelivery_does_not_duplicate_governed_write(self):
        spool = gi.Spool(":memory:")
        payload = gi.redact(self.payload())
        spool.add("12345678-abcd", "discussion_comment", payload)
        spool.add("12345678-abcd", "discussion_comment", payload)
        written = []
        gi.drain_once(spool, actor="tester", writer=written.append)
        self.assertEqual(len(written), 1)
        gi.drain_once(spool, actor="tester", writer=written.append)
        self.assertEqual(len(written), 1)


@requires_quipu
class DiscussionShapeTests(unittest.TestCase):
    def test_native_store_accepts_discussions_and_refuses_non_thread_target(self):
        payload = DiscussionTests().payload()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); db = str(root / "store.db")
            subprocess.run([str(QUIPU), "graph", "create", GRAPH, "--db", db], check=True, capture_output=True)
            for index, event in enumerate(["discussion", "discussion_comment"]):
                episode, _ = gi.episode_for(event, payload, actor="tester")
                path = root / f"episode{index}.json"; path.write_text(json.dumps(episode))
                subprocess.run([str(QUIPU), "episode", str(path), "--db", db,
                                "--timestamp", "2026-11-22T00:00:00Z",
                                "--base-ns", "http://aegis.gastown.local/ontology/"],
                               check=True, capture_output=True)
            ttl = subprocess.run([str(QUIPU), "export", "--graph", GRAPH, "--format", "turtle", "--db", db],
                                 check=True, capture_output=True, text=True).stdout
            def validate(data):
                path = root / "data.ttl"; path.write_text(data)
                return subprocess.run([str(QUIPU), "validate", "--shapes", str(ROOT / "shapes/github.shapes.ttl"),
                                       "--data", str(path)], capture_output=True, text=True)
            out = validate(ttl)
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            self.assertTrue(out.stdout.lstrip().startswith("valid"), out.stdout + out.stderr)
            # A declared class alone cannot pass the onGitHubThread constraint.
            self.assertIn("GitHubDiscussion", ttl)
            out = validate(ttl.replace("GitHubDiscussion", "GitHubRelease"))
            self.assertFalse(out.stdout.lstrip().startswith("valid"), out.stdout + out.stderr)
