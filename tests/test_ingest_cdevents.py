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

    # dearing's slice-2 ruling (aegis-rb3): a pipeline run is a deployment only
    # when its emitter declares an actual apply attempt; failed names the
    # ATTEMPTED artifact; a serving artifact is separate and only observed.
    HEALTHY = {"ok": 7, "changed": 2, "failures": 0, "unreachable": 0, "skipped": 0, "rescued": 0, "ignored": 0}

    def succeeded_event(self, **custom):
        """A clean apply: success word, healthy recap, observed serving artifact."""
        custom.setdefault("servingArtifactId", "pkg:generic/example-iac@5e1f0c9d8b7a6f5e4d3c2b1a0f9e8d7c6b5a4f3e")
        custom.setdefault("recap", dict(self.HEALTHY))
        event = self.run_event(**custom)
        event["data"]["subject"]["content"]["outcome"] = "success"
        return event

    def run_event(self, **custom):
        event = fixture("pipelinerun-failed")
        for key, value in custom.items():
            if value is None:
                event["data"]["customData"].pop(key, None)
            else:
                event["data"]["customData"][key] = value
        return event

    def test_a_failed_apply_names_only_what_it_attempted(self):
        body = self.body(fixture("pipelinerun-failed"))
        props = body["nodes"][0]["properties"]
        self.assertEqual("failed", props["deploymentOutcome"])
        self.assertTrue(props["attemptedArtifactId"].startswith("pkg:generic/example-iac@"))
        self.assertNotIn("artifactId", props, "never copied from the attempt")
        self.assertEqual("web", body["edges"][0]["target"])
        self.assertIn('subject.content.outcome="failure"', body["source"])
        self.assertIn('customData.recap={"changed":2,"failures":1,"ignored":0,"ok":7,"rescued":0,'
                      '"skipped":0,"unreachable":0}', body["source"])

    def test_a_failed_apply_keeps_an_observed_serving_artifact(self):
        serving = "pkg:generic/example-iac@0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e"
        props = self.node(self.run_event(servingArtifactId=serving))["properties"]
        self.assertEqual(serving, props["artifactId"])
        self.assertNotEqual(props["artifactId"], props["attemptedArtifactId"])

    def test_a_successful_apply_needs_its_serving_artifact(self):
        self.assertEqual("succeeded", self.node(self.succeeded_event())["properties"]["deploymentOutcome"])
        attempted_only = self.succeeded_event()
        del attempted_only["data"]["customData"]["servingArtifactId"]
        with self.assertRaises(ingest.Quarantined):
            self.body(attempted_only)

    def test_outcome_words_map_explicitly_and_unknown_words_quarantine(self):
        for word, want in [("success", "succeeded"), ("failure", "failed"), ("error", "failed")]:
            with self.subTest(word):
                # Generic producer: no declared emitter, so the recap stays provenance.
                event = self.run_event(servingArtifactId="pkg:generic/example-iac@aa", emitter=None)
                event["data"]["subject"]["content"]["outcome"] = word
                self.assertEqual(want, self.node(event)["properties"]["deploymentOutcome"])
        for word in ("cancelled", "skipped", "", None, 1):
            with self.subTest(word):
                event = fixture("pipelinerun-failed")
                event["data"]["subject"]["content"]["outcome"] = word
                with self.assertRaises(ingest.Quarantined):
                    self.body(event)

    def test_an_ansible_recap_must_agree_with_the_outcome_word(self):
        # dearing's review of PR 124: failed or unreachable above zero is a
        # failure whatever the word says; a contradiction is refused.
        self.assertEqual("succeeded", self.node(self.succeeded_event())["properties"]["deploymentOutcome"])
        for counter in ("failures", "unreachable"):
            with self.subTest(f"success word, {counter}=1"):
                recap = dict(self.HEALTHY, **{counter: 1})
                with self.assertRaises(ingest.Quarantined):
                    self.body(self.succeeded_event(recap=recap))
            with self.subTest(f"failure word, {counter}=1"):
                recap = dict(self.HEALTHY, **{counter: 1})
                self.assertEqual("failed", self.node(self.run_event(recap=recap))["properties"]["deploymentOutcome"])
        with self.subTest("failure word, healthy recap"):
            with self.assertRaises(ingest.Quarantined):
                self.body(self.run_event(recap=dict(self.HEALTHY)))

    def test_a_missing_or_partial_ansible_recap_is_unknown(self):
        partial = dict(self.HEALTHY)
        del partial["unreachable"]
        for name, recap in [("missing", None), ("partial", partial), ("not a dict", "failed=0"),
                            ("string counter", dict(self.HEALTHY, failures="0")),
                            ("bool counter", dict(self.HEALTHY, failures=False)),
                            ("negative counter", dict(self.HEALTHY, unreachable=-1))]:
            with self.subTest(name):
                event = self.succeeded_event()
                if recap is None:
                    del event["data"]["customData"]["recap"]
                else:
                    event["data"]["customData"]["recap"] = recap
                with self.assertRaises(ingest.Quarantined):
                    self.body(event)

    def test_the_recap_rule_binds_only_the_producer_it_names(self):
        # Without the declared emitter the counters are provenance only, so
        # the Ansible rule is not silently applied to every pipeline.
        recap = dict(self.HEALTHY, failures=1)
        props = self.node(self.succeeded_event(emitter=None, recap=recap))["properties"]
        self.assertEqual("succeeded", props["deploymentOutcome"])

    def test_no_actual_apply_attempt_is_not_a_deployment(self):
        # The measured case: lint failed on lock acquisition, so the apply step
        # was skipped. Workflow metadata may still carry service, artifact and
        # environment; without attempt evidence it is quarantined.
        for attempt in (None, False, "true", 1):
            with self.subTest(attempt):
                with self.assertRaises(ingest.Quarantined):
                    self.body(self.run_event(deploymentAttempt=attempt))

    def test_artifact_requirements_quarantine_never_guess(self):
        cases = {
            "missing attempted and serving": dict(attemptedArtifactId=None),
            "serving only": dict(attemptedArtifactId=None, servingArtifactId="pkg:generic/example-iac@aa"),
            "attempted not a purl": dict(attemptedArtifactId="5e1f0c9d"),
            "serving not a purl": dict(servingArtifactId="0d1e2f3a"),
            "no service": dict(service=None),
            "no environment": dict(environment=None),
        }
        for name, custom in cases.items():
            with self.subTest(name):
                with self.assertRaises(ingest.Quarantined):
                    self.body(self.run_event(**custom))

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
                if rule["class"] == "ChangeEvent":
                    actions = ({rule["action"]} if "action" in rule
                               else set(rule["actionFrom"]["map"].values()))
                    self.assertTrue(actions)
                    self.assertLessEqual(actions, {"created", "reviewed", "merged", "abandoned",
                                                   "ci-passed", "ci-failed"})
                    self.assertTrue(rule["fields"]["changeId"]["required"])
                    ingest.windows.window_iri(rule["window"], "2026-10")
                    continue
                if rule["class"] == "ReleaseEvent":
                    self.assertEqual(base, "dev.cdevents.artifact.published")
                    for field in ("artifactId", "changeRepository", "commitSha", "eventActor", "releaseTag"):
                        self.assertTrue(rule["fields"][field]["required"])
                    ingest.windows.window_iri(rule["window"], "2026-10")
                    continue
                self.assertEqual("DeploymentEvent", rule["class"])
                outcomes = ({rule["outcome"]} if "outcome" in rule
                            else set(rule["outcomeFrom"]["map"].values()))
                self.assertTrue(outcomes)
                self.assertLessEqual(outcomes, {"succeeded", "failed", "rolledback"})
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
