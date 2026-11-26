"""aegis-55ya: minted instance IRIs come from a configured base, not a private host.

Vocabulary (classes and properties) stays on the namespace the loaded shapes
gate on; that move is aegis-m5s.3. What this file pins is the instance side:
record keys and versions, topics, captures and entity_link's work-item
subject. With no configuration none of them is under the former aegis host;
configured with the aegis base they are exactly today's IRIs; and a record
already stored under the old base still recalls and is superseded on its own
key, with no stored data rewritten.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

from rdflib import RDF, Graph, URIRef

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from camayoc_cli import capture, memory, planes, promote  # noqa: E402

LEGACY = "http://aegis.gastown.local/ontology/"
NEUTRAL = planes.DEFAULT_INSTANCE_NS
ARGS = dict(rule="Push straight to main; rebase first.", issuedBy="https://example.org/e/operator",
            appliesTo="https://example.org/e/repo-goldblum", topic="push-policy")
TOPIC = "https://example.org/t/push-policy"
WHEN = {"CAMAYOC_NOW": "2026-11-26T03:00:00Z", "SHANTY_AGENT": "tester"}


class Writer:
    """Captures knots; answers the supersession lookup from `stored` rows."""

    def __init__(self, stored=None):
        self.writes, self.queries, self.stored = [], [], stored or []

    def knot(self, turtle, graph, actor, source):
        self.writes.append(turtle)

    def query(self, sparql, timeout=None):
        self.queries.append(sparql)
        return [r for r in self.stored if f"<{r['key']}>" in sparql]


def env(**extra):
    base = {k: v for k, v in os.environ.items() if k != "CAMAYOC_INSTANCE_NS"}
    return mock.patch.dict(os.environ, {**base, **WHEN, **extra}, clear=True)


def write(client):
    with mock.patch.object(memory, "entity_exists", return_value=True), \
            mock.patch.object(memory, "find_topic", return_value=TOPIC):
        return memory.record("directive", dict(ARGS), client=client)


def instance_terms(turtle: str) -> set[str]:
    """Every IRI in a subject or object position, rdf:type classes excluded."""
    g = Graph().parse(data=turtle, format="turtle")
    out = set()
    for s, p, o in g:
        for term in (s, o):
            if isinstance(term, URIRef) and not (p == RDF.type and term is o):
                out.add(str(term))
    return out


class UnconfiguredMintsNoOldInstanceIri(unittest.TestCase):
    def test_fresh_record_has_no_old_namespace_instance_iri(self):
        with env():
            client = Writer()
            result = write(client)
        self.assertEqual(0, result.code, result.lines)
        self.assertEqual(1, len(client.writes))
        terms = instance_terms(client.writes[0])
        self.assertEqual([], sorted(t for t in terms if t.startswith(LEGACY)))
        key = f"{NEUTRAL}mem/directive/repo-goldblum/push-policy"
        self.assertIn(key, terms)
        self.assertEqual(f"{key}/20261126T030000Z", result.data["iri"])

    def test_topic_capture_and_workitem_are_neutral(self):
        import entity_link
        with env():
            client = Writer()
            with mock.patch.object(memory, "find_topic", return_value=None):
                out = memory.add_topic("deploy-window", [], client=client)
            self.assertEqual(f"{NEUTRAL}mem/topic/deploy-window", out.data["iri"])
            self.assertTrue(capture.capture_iri({"at": "2026-11-26T03:00:00Z", "hash": "sha256:" + "a" * 64})
                            .startswith(f"{NEUTRAL}mem/capture/"))
            self.assertEqual(f"{NEUTRAL}workitem/", entity_link.workitem_ns())
        self.assertTrue(client.writes[0].startswith(f"<{NEUTRAL}mem/topic/deploy-window>"))

    def test_the_test_can_fail(self):
        """Negative control: the same check sees an old-base key when one is minted."""
        with env(CAMAYOC_INSTANCE_NS=LEGACY):
            client = Writer()
            write(client)
        self.assertTrue(any(t.startswith(LEGACY) for t in instance_terms(client.writes[0])))


class ConfiguredAegisBaseReproducesTodaysIris(unittest.TestCase):
    def test_record_topic_capture_workitem(self):
        import entity_link
        with env(CAMAYOC_INSTANCE_NS=LEGACY):
            result = write(Writer())
            with mock.patch.object(memory, "find_topic", return_value=None):
                topic = memory.add_topic("deploy-window", [], client=Writer())
            cap = capture.capture_iri({"at": "2026-11-26T03:00:00Z", "hash": "sha256:" + "b" * 64})
            work = entity_link.workitem_ns()
        self.assertEqual(f"{LEGACY}mem/directive/repo-goldblum/push-policy/20261126T030000Z", result.data["iri"])
        self.assertEqual(f"{LEGACY}mem/topic/deploy-window", topic.data["iri"])
        self.assertEqual(f"{LEGACY}mem/capture/20261126T030000Z-bbbbbbbbbbbb", cap)
        self.assertEqual(f"{LEGACY}workitem/", work)


class OldIriRecordsStillRead(unittest.TestCase):
    OLD_KEY = f"{LEGACY}mem/directive/repo-goldblum/push-policy"
    OLD = {"iri": OLD_KEY + "/20261001T000000Z", "key": OLD_KEY, "hash": "sha256:" + "0" * 64,
           "sourceKind": "observed"}

    def test_old_key_is_superseded_on_its_own_key(self):
        with env():
            client = Writer([self.OLD])
            result = write(client)
        self.assertEqual(0, result.code, result.lines)
        self.assertTrue(any(f"<{NEUTRAL}mem/directive/" in q and f"<{self.OLD_KEY}>" in q
                            for q in client.queries), "the lookup asks both bases")
        self.assertEqual(f"{self.OLD_KEY}/20261126T030000Z", result.data["iri"])
        self.assertIn(f"<{self.OLD['iri']}>", client.writes[0], "the new version supersedes the old one")
        self.assertNotIn(f"{NEUTRAL}mem/directive/", "".join(client.writes), "no second current key")

    def test_old_key_path_recalls(self):
        asked = []
        old_key = f"{LEGACY}mem/decision/repo-goldblum/history-test"
        old_row = {"iri": old_key + "/20261001T000000Z", "kind": "decision", "naturalKey": old_key,
                   "body": "A", "sourceKind": "declared", "recordedAt": "2026-10-01T00:00:00Z"}

        class Recaller:
            def ask(self, name, values):
                asked.append(values["key"])
                return {"rows": [old_row] if values["key"] == old_key else []}

        with env():
            out = memory.recall("decision", {}, history="repo-goldblum/history-test", client=Recaller())
        self.assertEqual(0, out.code, out.lines)
        self.assertEqual([f"{NEUTRAL}mem/decision/repo-goldblum/history-test", old_key], asked)
        self.assertEqual([old_row["iri"]], [r["iri"] for r in out.data["rows"]])

    def test_old_and_new_captures_are_both_promotable(self):
        with env():
            for base in (NEUTRAL, LEGACY):
                self.assertTrue(promote._is_capture(f"{base}mem/capture/20261126T030000Z-aaaaaaaaaaaa"))
            self.assertFalse(promote._is_capture("https://elsewhere.example/mem/capture/x"))

    def test_existing_old_topic_keeps_its_iri(self):
        old_topic = f"{LEGACY}mem/topic/deploy-window"
        with env(), mock.patch.object(memory, "find_topic", return_value=old_topic):
            out = memory.add_topic("deploy-window", [], client=Writer())
        self.assertEqual(("unchanged", old_topic), (out.data["status"], out.data["iri"]))


class ScriptsAndCliAgree(unittest.TestCase):
    def test_instance_base_matches_scripts_planes(self):
        import planes as repo_planes
        self.assertEqual(repo_planes.DEFAULT_INSTANCE_NS, planes.DEFAULT_INSTANCE_NS)
        self.assertEqual(repo_planes.LEGACY_INSTANCE_NS, planes.LEGACY_INSTANCE_NS)
        with env(CAMAYOC_INSTANCE_NS="https://example.org/deploy/"):
            self.assertEqual(repo_planes.instance_ns(), planes.instance_ns())


if __name__ == "__main__":
    unittest.main()
