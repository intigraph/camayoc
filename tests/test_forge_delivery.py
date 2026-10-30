"""One portable delivery -> standard event -> native governed episode."""
import copy
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import ingest_cdevents as ingress

spec = importlib.util.spec_from_file_location("forge_delivery", ROOT / "emitters/github/delivery.py")
emitter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(emitter)
SHA = "a" * 40
TIME = "2026-10-29T23:42:17Z"


def publication():
    return {"action": "published", "repository": {"full_name": "example/tool"},
            "sender": {"login": "publisher"},
            "release": {"id": 42, "draft": False, "tag_name": "v1.2.0",
                        "published_at": TIME, "target_commitish": "main"}}


class ForgeDelivery(unittest.TestCase):
    def event(self, payload=None, sha=SHA):
        return emitter.event_for("release", payload or publication(), release_commit=sha)

    def test_publication_roundtrip_has_real_time_actor_repo_and_resolved_sha(self):
        event = self.event()
        body = ingress.episode_for(event, actor="writer")
        self.assertEqual(body["graph"], "https://camayoc.local/window/release/2026-10")
        node = body["nodes"][0]
        self.assertEqual(node["type"], "ReleaseEvent")
        p = node["properties"]
        self.assertEqual((p["occurredAt"], p["eventActor"], p["changeRepository"], p["commitSha"]),
                         (TIME, "publisher", "example/tool", SHA))
        self.assertEqual(p["artifactId"], "pkg:generic/example/tool@v1.2.0")
        self.assertEqual(body["actor"], "writer")
        self.assertEqual(body, ingress.episode_for(copy.deepcopy(event), actor="writer"))

    def test_missing_or_branch_commit_never_becomes_a_release(self):
        for sha in ("", "main", "a" * 7):
            with self.subTest(sha=sha), self.assertRaises(ValueError):
                self.event(sha=sha)

    def test_ingress_itself_refuses_missing_release_evidence(self):
        for field in ("commitSha", "eventActor", "changeRepository", "releaseTag"):
            event = self.event()
            del event["data"]["customData"][field]
            with self.subTest(field=field), self.assertRaises(ingress.Quarantined):
                ingress.episode_for(event, actor="writer")

    def test_draft_or_nonpublication_does_not_emit(self):
        for change in ({"draft": True},):
            payload = publication()
            payload["release"].update(change)
            self.assertIsNone(self.event(payload))
        payload = publication()
        payload["action"] = "edited"
        self.assertIsNone(self.event(payload))

    def test_merge_roundtrip_uses_merge_commit_and_merger_not_sender(self):
        payload = {"action": "closed", "repository": {"full_name": "example/tool"},
                   "sender": {"login": "transport"}, "pull_request": {
                       "number": 3, "merged": True, "merge_commit_sha": SHA,
                       "merged_at": TIME, "merged_by": {"login": "merger"}}}
        event = emitter.event_for("pull_request", payload)
        props = ingress.episode_for(event, actor="writer")["nodes"][0]["properties"]
        self.assertEqual((props["changeAction"], props["eventActor"], props["commitSha"]),
                         ("merged", "merger", SHA))
        payload["pull_request"]["merged"] = False
        self.assertIsNone(emitter.event_for("pull_request", payload))

    def test_bad_time_and_missing_actor_refuse(self):
        for value in (None, "2026-02-30T01:00:00Z"):
            payload = publication()
            payload["release"]["published_at"] = value
            with self.assertRaises(ValueError):
                self.event(payload)
        payload = publication()
        payload["sender"]["login"] = " "
        with self.assertRaises(ValueError):
            self.event(payload)

    def test_forgejo_origin_retains_scheme_and_port_without_credentials(self):
        event = emitter.event_for("release", publication(), release_commit=SHA,
                                  forge="http://forge.example:3000")
        self.assertEqual(event["source"], "http://forge.example:3000/example/tool")
        for forge in ("https://user:password@forge.example", "https://forge.example/path",
                      "file://forge.example", "http://forge.example:0", "forge.example:99999"):
            with self.subTest(forge=forge), self.assertRaises(ValueError):
                emitter.event_for("release", publication(), release_commit=SHA, forge=forge)
