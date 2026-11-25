"""The alert shapes, against quipu's own SHACL engine (aegis-dc5, gap G2).

The quipu CLI is found by tests/quipu_bin_guard.py: where QUIPU_BIN is promised
(the integration job) this suite RUNS, elsewhere it skips with the reason.
One positive control per class and one negative per constraint a producer or
consumer depends on.
"""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from quipu_bin_guard import QUIPU, requires_quipu

ROOT = Path(__file__).resolve().parents[1]
SHAPES = ROOT / "shapes" / "alertmanager.shapes.ttl"
PREFIX = ("@prefix aegis: <http://aegis.gastown.local/ontology/> . "
          "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
          "aegis:rule-example a aegis:AlertRule ; rdfs:label \"ExampleDown\" .\n")
HEX64 = '"' + "a" * 64 + '"'
WHEN = '"2026-11-25T04:00:00Z"'

RECORDS = {
    "instance": ("aegis:alert-1", {
        "a": "aegis:AlertInstance",
        "rdfs:label": '"ExampleDown on host-a"',
        "aegis:sourceKind": '"observed"',
        "aegis:alertFingerprint": '"0123456789abcdef"',
        "aegis:alertName": '"ExampleDown"',
        "aegis:alertLabelsDigest": HEX64,
        "aegis:severity": '"warning"',
        "aegis:keeper": '"ops"',
        "aegis:alertHost": '"host-a"',
        "aegis:instanceOfAlertRule": "aegis:rule-example",
    }),
    "transition": ("aegis:alert-1-t1", {
        "a": "aegis:AlertTransition",
        "rdfs:label": '"ExampleDown firing"',
        "aegis:sourceKind": '"observed"',
        "aegis:transitionOf": "aegis:alert-1",
        "aegis:alertStatus": '"firing"',
        "aegis:alertTransitionKey": HEX64,
        "aegis:occurredAt": WHEN,
    }),
    "ack": ("aegis:alert-1-ack1", {
        "a": "aegis:AlertAcknowledgement",
        "rdfs:label": '"ExampleDown acknowledged"',
        "aegis:sourceKind": '"declared"',
        "aegis:acknowledgementOf": "aegis:alert-1",
        "aegis:acknowledgedBy": '"harding"',
        "aegis:acknowledgesTransition": "aegis:alert-1-t1",
        "aegis:alertEventKey": HEX64,
        "aegis:occurredAt": WHEN,
    }),
    "delivery": ("aegis:alert-1-d0", {
        "a": "aegis:AlertDelivery",
        "rdfs:label": '"ExampleDown to harding, tier 0"',
        "aegis:sourceKind": '"observed"',
        "aegis:deliveryOf": "aegis:alert-1",
        "aegis:deliveredTier": "0",
        "aegis:deliveredTo": '"harding"',
        "aegis:deliveryRoute": "aegis:route-ops",
        "aegis:alertEventKey": HEX64,
        "aegis:occurredAt": WHEN,
    }),
}


def render(changes: dict[str, dict[str, str | None]] | None = None) -> str:
    out = []
    for kind, (subject, fields) in RECORDS.items():
        fields = dict(fields)
        for key, value in (changes or {}).get(kind, {}).items():
            if value is None:
                fields.pop(key)
            else:
                fields[key] = value
        out.append(subject + " " + " ;\n  ".join(f"{k} {v}" for k, v in fields.items()) + " .\n")
    return "".join(out)


@requires_quipu
class AlertShapes(unittest.TestCase):
    def report(self, body: str) -> str:
        with tempfile.NamedTemporaryFile("w", suffix=".ttl", delete=False) as fh:
            fh.write(PREFIX + body)
        try:
            out = subprocess.run([str(QUIPU), "validate", "--shapes", str(SHAPES), "--data", fh.name],
                                 capture_output=True, text=True, timeout=60)
        finally:
            Path(fh.name).unlink()
        self.assertNotIn("error:", out.stderr.lower(), out.stderr)
        return out.stdout

    def assertConforms(self, body: str) -> None:
        out = self.report(body)
        self.assertTrue(out.lstrip().startswith("valid"), out)

    def assertViolates(self, body: str, path: str) -> None:
        # Every violation sits on the predicate named, so a syntax error or an
        # unrelated rule cannot pass as a negative. A wrongly typed value may
        # trip two constraints on that same predicate (datatype and range).
        out = self.report(body)
        self.assertTrue(out.lstrip().startswith("invalid:"), out)
        iri = ("http://www.w3.org/2000/01/rdf-schema#label" if path == "label"
               else f"http://aegis.gastown.local/ontology/{path}")
        lines = [line for line in out.splitlines() if "[Violation]" in line]
        self.assertTrue(lines, out)
        for line in lines:
            self.assertIn(f"(path: {iri})", line, out)

    def test_complete_records_conform(self) -> None:
        self.assertConforms(render())

    def test_optional_instance_facts_may_be_absent(self) -> None:
        self.assertConforms(render({"instance": {k: None for k in (
            "aegis:severity", "aegis:keeper", "aegis:alertHost", "aegis:instanceOfAlertRule")},
            "delivery": {"aegis:deliveryRoute": None},
            "ack": {"aegis:acknowledgesTransition": None}}))

    def test_resolved_is_a_transition_status(self) -> None:
        self.assertConforms(render({"transition": {"aegis:alertStatus": '"resolved"'}}))

    def test_fingerprint_must_be_sixteen_hex(self) -> None:
        for bad in ('"0123456789ABCDEF"', '"0123456789abcde"', '"0123456789abcdef0"'):
            with self.subTest(bad=bad):
                self.assertViolates(render({"instance": {"aegis:alertFingerprint": bad}}), "alertFingerprint")

    def test_instance_requires_its_identity(self) -> None:
        for key in ("aegis:alertFingerprint", "aegis:alertName", "aegis:alertLabelsDigest",
                    "aegis:sourceKind", "rdfs:label"):
            with self.subTest(key=key):
                self.assertViolates(render({"instance": {key: None}}), key.split(":")[1])

    def test_status_is_a_closed_set(self) -> None:
        for bad in ('"Firing"', '"pending"', '"acknowledged"'):
            with self.subTest(bad=bad):
                self.assertViolates(render({"transition": {"aegis:alertStatus": bad}}), "alertStatus")

    def test_transition_must_name_an_alert_instance(self) -> None:
        self.assertViolates(render({"transition": {"aegis:transitionOf": "aegis:rule-example"}}), "transitionOf")
        self.assertViolates(render({"transition": {"aegis:transitionOf": None}}), "transitionOf")

    def test_transition_needs_its_key_and_instants(self) -> None:
        for key in ("aegis:alertTransitionKey", "aegis:occurredAt"):
            with self.subTest(key=key):
                self.assertViolates(render({"transition": {key: None}}), key.split(":")[1])
        self.assertViolates(render({"transition": {"aegis:occurredAt": '"2026-11-25 04:00:00"'}}), "occurredAt")
        self.assertViolates(render({"transition": {"aegis:alertTransitionKey": '"abc"'}}), "alertTransitionKey")

    def test_one_status_per_transition(self) -> None:
        self.assertViolates(render({"transition": {"aegis:alertStatus": '"firing", "resolved"'}}), "alertStatus")

    def test_acknowledgement_needs_actor_and_instance(self) -> None:
        for key in ("aegis:acknowledgedBy", "aegis:acknowledgementOf", "aegis:alertEventKey"):
            with self.subTest(key=key):
                self.assertViolates(render({"ack": {key: None}}), key.split(":")[1])
        self.assertViolates(render({"ack": {"aegis:sourceKind": '"inferred"'}}), "sourceKind")
        self.assertViolates(render({"ack": {"aegis:acknowledgesTransition": "aegis:alert-1"}}), "acknowledgesTransition")

    def test_delivery_tier_is_a_nonnegative_integer(self) -> None:
        for bad in ("-1", '"0"', "1.5"):
            with self.subTest(bad=bad):
                self.assertViolates(render({"delivery": {"aegis:deliveredTier": bad}}), "deliveredTier")
        self.assertConforms(render({"delivery": {"aegis:deliveredTier": "3"}}))

    def test_delivery_needs_recipient_and_route_is_an_iri(self) -> None:
        self.assertViolates(render({"delivery": {"aegis:deliveredTo": None}}), "deliveredTo")
        self.assertViolates(render({"delivery": {"aegis:deliveryRoute": '"ops"'}}), "deliveryRoute")


if __name__ == "__main__":
    unittest.main()
