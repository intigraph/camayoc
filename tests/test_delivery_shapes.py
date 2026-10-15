"""The delivery shapes, against quipu's own SHACL engine (aegis-rb3, proposal 31).

The quipu CLI is found by tests/quipu_bin_guard.py: where QUIPU_BIN is promised
(the integration job) this suite RUNS, elsewhere it skips with the reason.
One positive control and one negative per constraint the record depends on.
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from quipu_bin_guard import QUIPU, requires_quipu

ROOT = Path(__file__).resolve().parents[1]
SHAPES = ROOT / "shapes" / "delivery.shapes.ttl"
PREFIX = ("@prefix aegis: <http://aegis.gastown.local/ontology/> . "
          "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n")

FIELDS = {
    "a": "aegis:DeploymentEvent",
    "rdfs:label": '"example-svc succeeded"',
    "aegis:sourceKind": '"observed"',
    "aegis:deployedService": "aegis:example-svc",
    "aegis:artifactId": '"pkg:github/example/example-svc@3392682256648f3ec088dd768b270a0097089a7a"',
    "aegis:environment": '"deploy-host.example.invalid"',
    "aegis:deploymentOutcome": '"succeeded"',
    "aegis:occurredAt": '"2026-10-14T19:41:59Z"',
    "aegis:cdeventsType": '"dev.cdevents.service.deployed.0.2.0"',
    "aegis:cloudEventSource": '"example-cd/deploy"',
    "aegis:cloudEventId": '"6c0d"',
}


def record(**changes: str | None) -> str:
    fields = dict(FIELDS)
    for key, value in changes.items():
        key = key.replace("__", ":")
        if value is None:
            fields.pop(key)
        else:
            fields[key] = value
    return "aegis:e1 " + " ;\n  ".join(f"{k} {v}" for k, v in fields.items()) + " .\n"


@requires_quipu
class DeliveryShapes(unittest.TestCase):
    def report(self, body: str) -> str:
        with tempfile.NamedTemporaryFile("w", suffix=".ttl", delete=False) as fh:
            fh.write(PREFIX + body)
        out = subprocess.run([str(QUIPU), "validate", "--shapes", str(SHAPES), "--data", fh.name],
                             capture_output=True, text=True)
        Path(fh.name).unlink()
        return out.stdout

    def valid(self, body: str) -> bool:
        return self.report(body).lstrip().startswith("valid")

    def test_positive_control(self):
        self.assertTrue(self.valid(record()))

    def test_rollback_keeps_both_artifacts(self):
        self.assertTrue(self.valid(record(
            aegis__deploymentOutcome='"rolledback"',
            aegis__attemptedArtifactId='"pkg:github/example/example-svc@339268225664"')))

    def test_negative_controls(self):
        # Each must fail with exactly ONE violation, on the constraint it names,
        # so a syntax error or an unrelated rule cannot pass as a negative.
        cases = {
            "removed is not an outcome": (record(aegis__deploymentOutcome='"removed"'), "In constraint", "deploymentOutcome"),
            "artifact not a purl": (record(aegis__artifactId='"3392682"'), "Pattern(", "artifactId"),
            "no time": (record(aegis__occurredAt=None), "MinCount(1)", "occurredAt"),
            "time not RFC 3339": (record(aegis__occurredAt='"2026-10-14 19:41"'), "Pattern(", "occurredAt"),
            "no event id": (record(aegis__cloudEventId=None), "MinCount(1)", "cloudEventId"),
            "no event source": (record(aegis__cloudEventSource=None), "MinCount(1)", "cloudEventSource"),
            "not a CDEvents type": (record(aegis__cdeventsType='"deploy"'), "Pattern(", "cdeventsType"),
            "not observed": (record(aegis__sourceKind='"inferred"'), "In constraint", "sourceKind"),
            "no service": (record(aegis__deployedService=None), "MinCount(1)", "deployedService"),
            "service is a literal": (record(aegis__deployedService='"example-svc"'), "NodeKind", "deployedService"),
            "two outcomes": (record(aegis__deploymentOutcome='"succeeded", "failed"'), "MaxCount(1)", "deploymentOutcome"),
        }
        for name, (body, constraint, path) in cases.items():
            with self.subTest(name):
                out = self.report(body)
                self.assertIn("invalid: 1 violation(s)", out)
                self.assertIn(constraint, out)
                self.assertIn(f"ontology/{path}", out)


    def test_the_artifact_a_record_names_depends_on_its_outcome(self):
        # dearing's slice-2 ruling (aegis-rb3): succeeded/rolledback name the
        # serving-after artifact; failed names the ATTEMPT, and a serving
        # artifact there is optional and only ever observed.
        serving = FIELDS["aegis:artifactId"]
        attempt = '"pkg:github/example/example-svc@97e84848ab4977739280e4ce3f9f683e437733ba"'
        valid = {
            "failed, attempted only": record(aegis__deploymentOutcome='"failed"', aegis__artifactId=None,
                                             aegis__attemptedArtifactId=attempt),
            "failed, attempted and observed serving": record(aegis__deploymentOutcome='"failed"',
                                                             aegis__attemptedArtifactId=attempt),
        }
        for name, body in valid.items():
            with self.subTest(name):
                self.assertTrue(self.valid(body), self.report(body))
        branch_failures = {
            "no serving artifact": record(aegis__artifactId=None),
            "failed, neither artifact": record(aegis__deploymentOutcome='"failed"', aegis__artifactId=None),
            # The bypass a plain artifactId | (failed + attempted) OR would allow.
            "failed, serving only": record(aegis__deploymentOutcome='"failed"', aegis__artifactId=serving),
            "succeeded, attempted only": record(aegis__artifactId=None, aegis__attemptedArtifactId=attempt),
            "rolledback, no serving artifact": record(aegis__deploymentOutcome='"rolledback"', aegis__artifactId=None,
                                                      aegis__attemptedArtifactId=attempt),
        }
        for name, body in branch_failures.items():
            with self.subTest(name):
                out = self.report(body)
                self.assertIn("invalid: 1 violation(s)", out)
                self.assertIn("OR not satisfied", out)
        constraint_failures = {
            "attempted not a purl": (record(aegis__deploymentOutcome='"failed"', aegis__artifactId=None,
                                            aegis__attemptedArtifactId='"339268"'), "Pattern("),
            "two attempted artifacts": (record(aegis__deploymentOutcome='"failed"', aegis__artifactId=None,
                                               aegis__attemptedArtifactId=attempt + ", " + serving), "MaxCount(1)"),
        }
        for name, (body, constraint) in constraint_failures.items():
            with self.subTest(name):
                out = self.report(body)
                self.assertIn("invalid: 1 violation(s)", out)
                self.assertIn(constraint, out)
                self.assertIn("ontology/attemptedArtifactId", out)

    def test_time_range_negatives(self):
        for value in ("2026-13-01T00:00:00Z", "2026-10-32T00:00:00Z", "2026-10-14T24:00:00Z",
                      "2026-10-14T12:60:00Z", "2026-10-14T23:59:60Z", "2026-10-14T12:00:00+24:00",
                      "2026-99-99T99:99:99Z"):
            with self.subTest(value):
                out = self.report(record(aegis__occurredAt=f'"{value}"'))
                self.assertIn("invalid: 1 violation(s)", out)
                self.assertIn("Pattern(", out)
                self.assertIn("ontology/occurredAt", out)

    def test_what_the_ingest_produces_conforms(self):
        """End to end on the real engine: each mapped fixture, as the ingest
        writes it, satisfies the shape, so the two halves cannot drift apart."""
        ingest = _ingest()
        fixtures = ROOT / "tests" / "fixtures" / "cdevents"
        serving = "pkg:generic/example-iac@0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e"
        cases = {name: json.loads((fixtures / f"{name}.json").read_text())
                 for name in ("deployed", "rolledback", "pipelinerun-failed")}
        observed = json.loads((fixtures / "pipelinerun-failed.json").read_text())
        observed["data"]["customData"]["servingArtifactId"] = serving
        cases["pipelinerun-failed+observed-serving"] = observed
        succeeded = json.loads(json.dumps(observed))
        succeeded["data"]["subject"]["content"]["outcome"] = "success"
        succeeded["data"]["customData"]["recap"] = dict(succeeded["data"]["customData"]["recap"], failures=0)
        cases["pipelinerun-succeeded"] = succeeded
        for name, event in cases.items():
            with self.subTest(name):
                body = ingest.episode_for(event, actor="tester")
                node = body["nodes"][0]
                fields = {"a": "aegis:DeploymentEvent",
                          "rdfs:label": json.dumps(node["properties"].get("label", node["name"])),
                          "aegis:deployedService": "aegis:" + body["edges"][0]["target"]}
                for key, value in node["properties"].items():
                    if key != "label":
                        fields[f"aegis:{key}"] = json.dumps(value)
                ttl = "aegis:e1 " + " ;\n  ".join(f"{k} {v}" for k, v in fields.items()) + " .\n"
                self.assertTrue(self.valid(ttl), self.report(ttl))

    def test_calendar_validity_is_the_producers_half_of_the_contract(self):
        """The shape is lexical + range; the producer proves the calendar.

        For each value: what the shape says and what the producer does, side by
        side, so neither half can quietly widen.
        """
        ingest = _ingest()
        for value, shape_ok, producer_ok in (
                ("2024-02-29T12:00:00Z", True, True),
                ("2026-10-31T23:30:00-05:00", True, True),
                ("2026-10-14T19:41:59.5Z", True, True),
                ("2026-02-30T12:00:00Z", True, False),   # in range, not a date
                ("2025-02-29T00:00:00Z", True, False),
                ("2026-10-14T24:00:00Z", False, False)):
            with self.subTest(value):
                self.assertIs(shape_ok, self.valid(record(aegis__occurredAt=f'"{value}"')))
                try:
                    ingest.event_month(value)
                    accepted = True
                except ingest.Quarantined:
                    accepted = False
                self.assertIs(producer_ok, accepted)


def _ingest():
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("ingest_cdevents", ROOT / "scripts/ingest_cdevents.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProducerMatchesShape(unittest.TestCase):
    """Every property the ingest writes is a predicate this file declares."""

    def test_ingest_properties_are_declared(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        spec = importlib.util.spec_from_file_location("ingest_cdevents", ROOT / "scripts/ingest_cdevents.py")
        ingest = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ingest)
        declared = set(re.findall(r"^aegis:(\w+) a rdf:Property", SHAPES.read_text(), re.M))
        for fixture in ("deployed", "rolledback"):
            event = json.loads((ROOT / f"tests/fixtures/cdevents/{fixture}.json").read_text())
            body = ingest.episode_for(event, actor="t")
            props = set(body["nodes"][0]["properties"]) - {"sourceKind"}
            relations = {edge["relation"] for edge in body["edges"]}
            with self.subTest(fixture):
                self.assertEqual(set(), (props | relations) - declared)
                self.assertEqual(body["nodes"][0]["type"],
                                 re.search(r"sh:targetClass aegis:(\w+)", SHAPES.read_text()).group(1))


if __name__ == "__main__":
    unittest.main()
