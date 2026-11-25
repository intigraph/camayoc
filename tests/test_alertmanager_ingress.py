"""Alertmanager webhook -> governed alert records (aegis-dc5 G1).

Offline: the mapping is a pure function. Where QUIPU_BIN is promised, the
mapped records are also validated against shapes/alertmanager.shapes.ttl by
quipu's own SHACL engine, so mapping and shape cannot drift apart.
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from quipu_bin_guard import QUIPU, requires_quipu

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import alertmanager_ingress as ai  # noqa: E402
from ingest_cdevents import Quarantined  # noqa: E402

FIRING = {
    "status": "firing",
    "labels": {"alertname": "ExampleDown", "severity": "warning", "keeper": "ops",
               "instance": "host-a:9100", "job": "node"},
    "annotations": {"summary": "never stored"},
    "startsAt": "2026-11-25T04:00:00.123Z",
    "endsAt": "2026-11-25T04:15:00Z",
    "generatorURL": "http://prometheus.example.invalid/graph",
    "fingerprint": "0123456789abcdef",
}
NOTIFICATION = {
    "version": "4", "groupKey": "{}:{alertname=\"ExampleDown\"}", "truncatedAlerts": 0,
    "status": "firing", "receiver": "camayoc", "groupLabels": {"alertname": "ExampleDown"},
    "commonLabels": {}, "commonAnnotations": {}, "externalURL": "http://am.example.invalid",
    "alerts": [FIRING],
}


def notification(*alerts: dict, **top: object) -> dict:
    body = copy.deepcopy(NOTIFICATION)
    body["alerts"] = [copy.deepcopy(a) for a in alerts] if alerts else body["alerts"]
    body.update(top)
    return body


def resolved(**changes: object) -> dict:
    alert = dict(FIRING, status="resolved", endsAt="2026-11-25T04:20:00Z")
    alert.update(changes)
    return alert


class Mapping(unittest.TestCase):
    def one(self, payload: dict) -> dict:
        bodies = ai.episodes_for(payload, actor="camayoc-test")
        self.assertEqual(len(bodies), 1)
        return bodies[0]

    def test_firing_alert_maps_to_instance_and_transition(self) -> None:
        body = self.one(notification())
        instance, transition = body["nodes"]
        self.assertEqual(instance["name"], "alert-0123456789abcdef")
        self.assertEqual(instance["type"], "AlertInstance")
        self.assertEqual(instance["properties"]["alertName"], "ExampleDown")
        self.assertEqual(instance["properties"]["severity"], "warning")
        self.assertEqual(instance["properties"]["keeper"], "ops")
        self.assertEqual(instance["properties"]["alertHost"], "host-a:9100")
        self.assertEqual(transition["type"], "AlertTransition")
        self.assertEqual(transition["properties"]["alertStatus"], "firing")
        self.assertEqual(transition["properties"]["occurredAt"], "2026-11-25T04:00:00.123Z")
        self.assertEqual(body["edges"], [{"source": transition["name"], "target": instance["name"],
                                          "relation": "transitionOf"}])
        self.assertTrue(body["graph"].endswith("/alerts/2026-11"), body["graph"])

    def test_label_values_and_annotations_are_not_stored(self) -> None:
        text = json.dumps(self.one(notification()))
        self.assertNotIn("never stored", text)
        self.assertNotIn('"job"', text)
        self.assertNotIn("prometheus.example.invalid", text)
        digest = self.one(notification())["nodes"][0]["properties"]["alertLabelsDigest"]
        self.assertEqual(digest, ai.labels_digest(FIRING["labels"]))

    def test_repeat_interval_resend_is_byte_identical(self) -> None:
        first = self.one(notification())
        # Alertmanager re-sends with a new group key / receiver context and a
        # moved predicted endsAt; the transition is the same fact.
        again = notification(dict(FIRING, endsAt="2026-11-25T04:19:00Z"), groupKey="other", receiver="r2")
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(self.one(again), sort_keys=True))

    def test_resolution_is_a_distinct_transition_dated_by_its_end(self) -> None:
        fire = self.one(notification())
        res = self.one(notification(resolved()))
        self.assertEqual(fire["nodes"][0], res["nodes"][0])  # same instance, byte-identical
        self.assertNotEqual(fire["nodes"][1]["name"], res["nodes"][1]["name"])
        self.assertEqual(res["nodes"][1]["properties"]["alertStatus"], "resolved")
        self.assertEqual(res["nodes"][1]["properties"]["occurredAt"], "2026-11-25T04:20:00Z")

    def test_a_new_firing_after_resolution_is_a_new_transition(self) -> None:
        a = self.one(notification())
        b = self.one(notification(dict(FIRING, startsAt="2026-11-25T05:00:00Z")))
        self.assertNotEqual(a["nodes"][1]["name"], b["nodes"][1]["name"])

    def test_resolution_month_picks_the_window(self) -> None:
        body = self.one(notification(resolved(startsAt="2026-11-30T23:59:00Z", endsAt="2026-12-01T00:01:00Z")))
        self.assertTrue(body["graph"].endswith("/alerts/2026-12"), body["graph"])

    def test_optional_routing_labels_may_be_absent(self) -> None:
        bare = dict(FIRING, labels={"alertname": "ExampleDown"})
        props = self.one(notification(bare))["nodes"][0]["properties"]
        self.assertEqual(set(props), {"sourceKind", "alertFingerprint", "alertName", "alertLabelsDigest"})

    def test_host_label_precedence(self) -> None:
        labels = dict(FIRING["labels"], host="named-host")
        props = self.one(notification(dict(FIRING, labels=labels)))["nodes"][0]["properties"]
        self.assertEqual(props["alertHost"], "named-host")

    def test_duplicate_alert_in_one_notification_is_kept_once(self) -> None:
        self.assertEqual(len(ai.episodes_for(notification(FIRING, FIRING), actor="t")), 1)

    def test_grouped_alerts_each_get_records(self) -> None:
        other = dict(FIRING, fingerprint="fedcba9876543210", labels={"alertname": "OtherDown"})
        names = [b["nodes"][0]["name"] for b in ai.episodes_for(notification(FIRING, other), actor="t")]
        self.assertEqual(names, ["alert-0123456789abcdef", "alert-fedcba9876543210"])

    def test_quarantine_never_guesses(self) -> None:
        cases = {
            "unknown version": notification(version="3"),
            "no alerts": notification(alerts=[]),
            "not an object": [],
            "bad status": notification(dict(FIRING, status="pending")),
            "uppercase fingerprint": notification(dict(FIRING, fingerprint="0123456789ABCDEF")),
            "short fingerprint": notification(dict(FIRING, fingerprint="0123")),
            "no fingerprint": notification({k: v for k, v in FIRING.items() if k != "fingerprint"}),
            "impossible start": notification(dict(FIRING, startsAt="2026-02-30T00:00:00Z")),
            "non RFC 3339 start": notification(dict(FIRING, startsAt="2026-11-25 04:00")),
            "resolved without end": notification(resolved(endsAt=ai.ZERO_TIME)),
            "no alertname": notification(dict(FIRING, labels={"severity": "warning"})),
            "non-string label": notification(dict(FIRING, labels={"alertname": "X", "n": 1})),
        }
        for name, payload in cases.items():
            with self.subTest(name), self.assertRaises(Quarantined):
                ai.episodes_for(payload, actor="t")

    def test_one_bad_alert_quarantines_the_whole_notification(self) -> None:
        with self.assertRaises(Quarantined):
            ai.episodes_for(notification(FIRING, dict(FIRING, fingerprint="bad")), actor="t")


def turtle(bodies: list[dict]) -> str:
    """The mapped records as Turtle, the way the governed writer types them."""
    out = ["@prefix aegis: <http://aegis.gastown.local/ontology/> .",
           "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> ."]
    for body in bodies:
        for node in body["nodes"]:
            out.append(f"aegis:{node['name']} a aegis:{node['type']} ; rdfs:label {json.dumps(node['description'])} .")
            for key, value in node["properties"].items():
                out.append(f"aegis:{node['name']} aegis:{key} {json.dumps(value)} .")
        for edge in body["edges"]:
            out.append(f"aegis:{edge['source']} aegis:{edge['relation']} aegis:{edge['target']} .")
    return "\n".join(out) + "\n"


@requires_quipu
class MappedRecordsConform(unittest.TestCase):
    def validate(self, bodies: list[dict]) -> str:
        with tempfile.NamedTemporaryFile("w", suffix=".ttl", delete=False) as fh:
            fh.write(turtle(bodies))
        try:
            out = subprocess.run([str(QUIPU), "validate", "--shapes", str(ROOT / "shapes/alertmanager.shapes.ttl"),
                                  "--data", fh.name], capture_output=True, text=True, timeout=60)
        finally:
            Path(fh.name).unlink()
        return out.stdout

    def test_firing_resolved_and_bare_records_conform(self) -> None:
        bodies = (ai.episodes_for(notification(), actor="t")
                  + ai.episodes_for(notification(resolved()), actor="t")
                  + ai.episodes_for(notification(dict(FIRING, fingerprint="fedcba9876543210",
                                                      labels={"alertname": "Bare"})), actor="t"))
        out = self.validate(bodies)
        self.assertTrue(out.lstrip().startswith("valid"), out)

    def test_replayed_records_still_conform(self) -> None:
        # The store sees a re-send as the same triples again: still one value
        # per maxCount-1 property.
        bodies = ai.episodes_for(notification(), actor="t") * 2
        out = self.validate(bodies)
        self.assertTrue(out.lstrip().startswith("valid"), out)


if __name__ == "__main__":
    unittest.main()
