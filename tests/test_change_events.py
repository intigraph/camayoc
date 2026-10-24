"""Change lifecycle CDEvents become governed ChangeEvent records (aegis-rb3 lead time).

Lead time is merge -> the first succeeded deployment that INCLUDES the merge
commit, so both halves are tested here: the change steps, and the deployment's
includesCommit list that the join reads.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("ingest_cdevents", ROOT / "scripts/ingest_cdevents.py")
ingest = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(ingest)

FIX = ROOT / "tests/fixtures/cdevents"
SHA_A = "3392682256648f3ec088dd768b270a0097089a7a"
SHA_B = "97e84848ab4977739280e4ce3f9f683e437733ba"


def fixture(name: str) -> dict:
    return json.loads((FIX / f"{name}.json").read_text())


def change(action: str, **custom) -> dict:
    """The merged fixture re-typed as another lifecycle step."""
    event = fixture("change-merged")
    kind = f"dev.cdevents.change.{action}.0.2.0"
    event["type"] = event["data"]["context"]["type"] = kind
    event["id"] = event["data"]["context"]["id"] = f"example/example-svc#42/{action}"
    event["data"]["customData"] = custom
    return event


class ChangeEventTests(unittest.TestCase):
    def body(self, payload):
        return ingest.episode_for(payload, actor="tester")

    def props(self, payload):
        return self.body(payload)["nodes"][0]["properties"]

    def quarantined(self, payload, reason: str):
        with self.assertRaises(ingest.Quarantined) as caught:
            self.body(payload)
        self.assertIn(reason, str(caught.exception))

    def test_merge_maps_to_a_change_event_naming_its_merge_commit(self):
        body = self.body(fixture("change-merged"))
        node = body["nodes"][0]
        self.assertEqual("ChangeEvent", node["type"])
        self.assertTrue(node["name"].startswith("change-event-"))
        self.assertEqual("https://camayoc.local/window/change/2026-10", body["graph"])
        self.assertEqual([], body["edges"])
        props = node["properties"]
        self.assertEqual({"sourceKind": "observed", "changeAction": "merged",
                          "occurredAt": "2026-10-14T18:02:11Z",
                          "cdeventsType": "dev.cdevents.change.merged.0.2.0",
                          "cloudEventSource": "example-forge/changes",
                          "cloudEventId": "example/example-svc#42/merged",
                          "changeId": "example/example-svc#42",
                          "changeRepository": "example/example-svc",
                          "commitSha": SHA_A}, props)

    def test_each_lifecycle_step_keeps_its_action(self):
        for action in ("created", "reviewed", "abandoned"):
            with self.subTest(action):
                self.assertEqual(action, self.props(change(action))["changeAction"])
        props = self.props(change("reviewed", reviewState="approved", commitSha=SHA_B))
        self.assertEqual(("approved", SHA_B), (props["reviewState"], props["commitSha"]))

    def test_a_merge_without_its_commit_is_quarantined(self):
        # Without the merge commit no deployment could ever be joined to it.
        self.quarantined(change("merged"), "a merged change step needs commitSha")

    def test_malformed_identity_is_quarantined(self):
        event = fixture("change-merged")
        event["data"]["subject"]["id"] = "example-svc 42"
        self.quarantined(event, "changeId")
        event = fixture("change-merged")
        event["data"]["customData"]["commitSha"] = "33926822"
        self.quarantined(event, "commitSha")

    def test_ci_result_maps_through_the_outcome_word(self):
        self.assertEqual("ci-passed", self.props(fixture("testsuiterun-passed"))["changeAction"])
        for word, action in (("fail", "ci-failed"), ("error", "ci-failed")):
            event = fixture("testsuiterun-passed")
            event["data"]["subject"]["content"]["outcome"] = word
            with self.subTest(word):
                self.assertEqual(action, self.props(event)["changeAction"])
        cancelled = fixture("testsuiterun-passed")
        cancelled["data"]["subject"]["content"]["outcome"] = "cancel"
        self.quarantined(cancelled, "is not mapped")
        headless = fixture("testsuiterun-passed")
        del headless["data"]["customData"]["commitSha"]
        self.quarantined(headless, "required field commitSha")

    def test_redelivery_is_byte_identical(self):
        one = json.dumps(self.body(fixture("change-merged")), sort_keys=True)
        two = json.dumps(self.body(copy.deepcopy(fixture("change-merged"))), sort_keys=True)
        self.assertEqual(one, two)


class DeploymentIncludesCommitTests(unittest.TestCase):
    def deployed(self, commits):
        event = fixture("deployed")
        event["data"]["customData"] = {"includedCommits": commits}
        return event

    def test_included_commits_ride_as_a_multi_valued_property(self):
        props = ingest.episode_for(self.deployed([SHA_A, SHA_B]), actor="t")["nodes"][0]["properties"]
        self.assertEqual([SHA_A, SHA_B], props["includesCommit"])

    def test_a_deployment_without_the_list_is_unchanged(self):
        props = ingest.episode_for(fixture("deployed"), actor="t")["nodes"][0]["properties"]
        self.assertNotIn("includesCommit", props)

    def test_malformed_lists_are_quarantined(self):
        for name, value in (("empty", []), ("bare string", SHA_A), ("short sha", [SHA_A, "3392682"]),
                            ("non-string", [SHA_A, 7])):
            with self.subTest(name), self.assertRaises(ingest.Quarantined):
                ingest.episode_for(self.deployed(value), actor="t")


if __name__ == "__main__":
    unittest.main()
