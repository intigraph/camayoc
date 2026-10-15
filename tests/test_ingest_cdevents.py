"""CDEvents enter through Camayoc as governed native deployment records."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("ingest_cdevents", ROOT / "scripts/ingest_cdevents.py")
ingest = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(ingest)

FIX = ROOT / "tests/fixtures/cdevents"


def fixture(name: str) -> dict:
    return json.loads((FIX / f"{name}.json").read_text())


class CDEventsIngressTests(unittest.TestCase):
    def body(self, payload):
        return ingest.episode_for(payload, actor="tester")

    def node(self, payload):
        return self.body(payload)["nodes"][0]

    def test_success_fixture_maps_to_a_succeeded_deployment(self):
        body = self.body(fixture("deployed"))
        node = body["nodes"][0]
        self.assertEqual("DeploymentEvent", node["type"])
        props = node["properties"]
        self.assertEqual("observed", props["sourceKind"])
        self.assertEqual("succeeded", props["deploymentOutcome"])
        self.assertEqual("2026-10-14T19:41:59Z", props["occurredAt"])
        self.assertTrue(props["artifactId"].startswith("pkg:github/example/example-svc@3392682"))
        self.assertEqual("deploy-host.example.invalid", props["environment"])
        self.assertNotIn("attemptedArtifactId", props)
        self.assertEqual([{"source": node["name"], "target": "example-svc",
                           "relation": "deployedService"}], body["edges"])

    def test_failure_fixture_maps_to_rolledback_with_the_attempt(self):
        props = self.node(fixture("rolledback"))["properties"]
        self.assertEqual("rolledback", props["deploymentOutcome"])
        self.assertTrue(props["artifactId"].endswith("@97e84848ab4977739280e4ce3f9f683e437733ba"))
        self.assertTrue(props["attemptedArtifactId"].endswith("@339268225664"))

    def test_unverified_rollback_is_a_failure(self):
        event = fixture("rolledback")
        event["data"]["customData"]["rollbackVerified"] = False
        self.assertEqual("failed", self.node(event)["properties"]["deploymentOutcome"])

    def test_lands_in_the_month_window_not_a_plane(self):
        body = self.body(fixture("deployed"))
        self.assertEqual(ingest.windows.window_iri("deployment", "2026-10"), body["graph"])
        self.assertEqual(("deployment", "2026-10"), ingest.window_of(body))
        self.assertNotIn(body["graph"], {ingest.planes.plane_for(k) for k in ("observed", "declared")})

    def test_redelivery_is_byte_identical_and_source_names_the_mapping(self):
        a, b = self.body(fixture("deployed")), self.body(fixture("deployed"))
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))
        _, sha = ingest.load_mapping()
        self.assertIn(f"mapping={sha[:16]}", a["source"])
        other = fixture("deployed")
        other["id"] = other["data"]["context"]["id"] = "different"
        self.assertNotEqual(a["nodes"][0]["name"], self.node(other)["name"])

    def test_bare_cdevent_equals_its_cloudevent(self):
        wrapped, bare = self.body(fixture("deployed")), self.body(fixture("bare-cdevent"))
        self.assertEqual(wrapped["nodes"], bare["nodes"])
        self.assertEqual(wrapped["graph"], bare["graph"])

    def test_unmappable_events_are_quarantined_never_guessed(self):
        base = fixture("deployed")
        def mutate(fn):
            event = copy.deepcopy(base)
            fn(event)
            return event
        cases = {
            "unmapped type": fixture("unmapped-type"),
            "not an object": [],
            "no envelope": {"hello": "world"},
            "missing id": mutate(lambda e: e.pop("id")),
            "type disagreement": mutate(lambda e: e.update(type="dev.cdevents.service.upgraded.0.2.0")),
            "unsupported cloudevents": mutate(lambda e: e.update(specversion="0.3")),
            "unsupported cdevents": mutate(lambda e: e["data"]["context"].update(version="1.0.0")),
            "unmapped major": mutate(lambda e: (e.update(type="dev.cdevents.service.deployed.1.0.0"),
                                                e["data"]["context"].update(type="dev.cdevents.service.deployed.1.0.0"))),
            "bad time": mutate(lambda e: e.update(time="yesterday")),
            "missing artifact": mutate(lambda e: e["data"]["subject"]["content"].pop("artifactId")),
            "non-string field": mutate(lambda e: e["data"]["subject"].update(id=7)),
        }
        for name, event in cases.items():
            with self.subTest(name), self.assertRaises(ingest.Quarantined):
                self.body(event)

    def with_time(self, value: str) -> dict:
        event = fixture("deployed")
        event["time"] = event["data"]["context"]["timestamp"] = value
        return event

    def test_calendar_valid_times_are_accepted_verbatim(self):
        # dearing review of #122: validated time, never normalized.
        for value, month in (("2024-02-29T12:00:00Z", "2024-02"),        # leap day
                             ("2026-10-14T19:41:59.123456789Z", "2026-10"),  # fraction
                             ("2026-10-31T23:30:00-05:00", "2026-10"),   # local date, offset
                             ("2026-12-31T23:59:59+14:00", "2026-12")):
            with self.subTest(value):
                body = self.body(self.with_time(value))
                self.assertEqual(value, body["nodes"][0]["properties"]["occurredAt"])
                self.assertEqual(ingest.windows.window_iri("deployment", month), body["graph"])

    def test_impossible_times_are_quarantined_before_a_window_is_chosen(self):
        for value in ("2026-02-30T12:00:00Z",   # day 30 of February
                      "2025-02-29T00:00:00Z",   # not a leap year
                      "2026-04-31T00:00:00Z",
                      "2026-10-14T24:00:00Z",
                      "2026-10-14T12:60:00Z",
                      "2026-10-14T23:59:60Z",   # leap second: not representable
                      "2026-10-14T12:00:00+24:00",
                      "2026-10-14T12:00:00+05:60",
                      "2026-99-99T99:99:99Z",
                      "2026-13-01T00:00:00Z"):
            with self.subTest(value), \
                 patch.object(ingest.windows, "window_iri", side_effect=AssertionError("window chosen")):
                with self.assertRaises(ingest.Quarantined):
                    self.body(self.with_time(value))

    def test_mapping_file_is_well_formed(self):
        mapping, _ = ingest.load_mapping()
        for base, rule in mapping["types"].items():
            with self.subTest(base):
                self.assertRegex(base, r"^dev\.cdevents\.[a-z]+\.[a-z]+$")
                self.assertIn(rule["outcome"], {"succeeded", "failed", "rolledback"})
                self.assertTrue(rule["fields"]["service"]["required"])
                ingest.windows.window_iri(rule["window"], "2026-10")

    def test_cli_parks_with_status_2(self):
        import subprocess
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts/ingest_cdevents.py"), "--actor", "t",
             str(FIX / "unmapped-type.json")], capture_output=True, text=True, check=False)
        self.assertEqual(2, proc.returncode)
        self.assertEqual("quarantined", json.loads(proc.stdout)["outcome"])


if __name__ == "__main__":
    unittest.main()
