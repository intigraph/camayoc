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
            "no serving artifact": (record(aegis__artifactId=None), "MinCount(1)", "artifactId"),
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
