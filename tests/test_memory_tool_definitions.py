"""Agent-memory tool definitions: shape + sidecar -> generated surfaces.

docs/design/agent-memory-entities.md §3. The drift test is the gate: every
generated file must equal what the shape and sidecars generate today, so a
field added to the shape without regenerating (or a hand edit to a generated
file) goes red here with the one command that fixes it.
"""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from rdflib_guard import requires_rdflib  # noqa: E402

import memory_secret_rules as msr  # noqa: E402
import memory_tools as mt  # noqa: E402

FIRST_ENTITIES = {"decision", "directive", "preference", "handoff", "reference"}


@requires_rdflib
class DefinitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shapes = mt.read_shapes()
        cls.sidecars = mt.read_sidecars()

    def test_generated_surfaces_match_definitions(self):
        stale = mt.drift(mt.outputs(self.shapes, self.sidecars))
        self.assertEqual([], stale, f"regenerate with: {mt.REGEN}")

    def test_definitions_lint_clean(self):
        self.assertEqual([], mt.lint(self.shapes, self.sidecars))

    def test_first_entities_are_defined(self):
        self.assertEqual(FIRST_ENTITIES, set(self.sidecars))
        doc, _ = mt.build(self.shapes, self.sidecars)
        self.assertEqual(FIRST_ENTITIES, set(doc["entities"]))
        for name, entity in doc["entities"].items():
            with self.subTest(entity=name):
                self.assertEqual(f"record_{name}", entity["verb"])
                self.assertTrue(entity["recall"], "every entity has a recall query")
                for f in entity["fields"]:
                    self.assertTrue(f["description"] and f["message"] and f["flag"])

    def test_memory_decision_is_not_the_governance_decision(self):
        """§1.3: the memory Decision keeps aegis:Decision; who ruled is an IRI on
        aegis:decidedBy, never the governance aegis:by literal."""
        decision = self.shapes["aegis:MemoryDecisionShape"]
        self.assertEqual("aegis:Decision", decision["target"])
        paths = {f["name"]: f["path"] for f in decision["fields"]}
        self.assertEqual("aegis:decidedBy", paths["by"])
        self.assertNotIn("aegis:by", paths.values())
        for gate_field in ("outcome", "evidenceHash"):
            self.assertNotIn(gate_field, paths)

    def test_lint_refuses_mincount_without_message(self):
        shapes = copy.deepcopy(self.shapes)
        shapes["aegis:MemoryDecisionShape"]["fields"][0]["message"] = None
        errors = mt.lint(shapes, self.sidecars)
        self.assertTrue(any("minCount without sh:message" in e for e in errors), errors)

    def test_lint_refuses_a_message_that_does_not_name_its_flag(self):
        shapes = copy.deepcopy(self.shapes)
        shapes["aegis:MemoryDirectiveShape"]["fields"][0]["message"] = "Not recorded: needs a rule."
        errors = mt.lint(shapes, self.sidecars)
        self.assertTrue(any("must name its flag --rule" in e for e in errors), errors)

    def test_lint_refuses_field_definitions_in_a_sidecar(self):
        sidecars = copy.deepcopy(self.sidecars)
        sidecars["decision"]["fields"] = [{"name": "chose"}]
        errors = mt.lint(self.shapes, sidecars)
        self.assertTrue(any("fields live in the shape" in e for e in errors), errors)

    def test_lint_refuses_a_key_part_the_shape_does_not_have(self):
        sidecars = copy.deepcopy(self.sidecars)
        sidecars["reference"]["key"] = ["subject", "purpose"]
        errors = mt.lint(self.shapes, sidecars)
        self.assertTrue(any("'purpose' is not a field" in e for e in errors), errors)

    def test_check_mode_reports_drift_and_the_fix(self):
        files = mt.outputs(self.shapes, self.sidecars)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "definitions.json"
            path.write_text("{}\n")
            original = mt.ROOT
            try:
                mt.ROOT = Path(tmp)
                stale = mt.drift({path: files[mt.DEFINITIONS]})
            finally:
                mt.ROOT = original
        self.assertEqual(["definitions.json: differs from what the definitions generate"], stale)

    def test_examples_are_complete_and_secret_free(self):
        for name, car in self.sidecars.items():
            with self.subTest(entity=name):
                flat = {k: " ".join(v) if isinstance(v, list) else v for k, v in car["example"].items()}
                self.assertEqual([], msr.refusals(flat))

    def test_recall_queries_carry_the_common_filters(self):
        _, queries = mt.build(self.shapes, self.sidecars)
        for name, manifest in queries.items():
            if name == "mem_index_since.json":
                continue  # the bobbin mirror feed; its own contract test is below
            with self.subTest(query=name):
                template = manifest["template"]
                self.assertIn("LIMIT 51", template)
                self.assertIn("FROM <{declared}> FROM <{records}> FROM <{inferred}>", template)
                if "history" not in name:
                    self.assertIn("FILTER NOT EXISTS { ?iri aegis:supersededBy ?newer }", template)
                    self.assertIn("aegis:expiresAt", template)
        self.assertIn("{fresh_since}", queries["mem_handoff.json"]["template"])

    def test_index_since_matches_the_bobbin_contract(self):
        """bobbin src/memory_recall.rs: called with only `since`; columns, UNION status, paging."""
        _, queries = mt.build(self.shapes, self.sidecars)
        q = queries["mem_index_since.json"]
        params = {p["name"]: p for p in q["params"]}
        self.assertEqual({"since", "declared", "records", "inferred"}, set(params))
        self.assertTrue(all(p["required"] is False and "default" in p for p in params.values()))
        self.assertEqual("", params["since"]["default"])
        t = q["template"]
        select = t.split("SELECT", 1)[1].split("FROM", 1)[0]
        for col in ("?iri", "?naturalKey", "?kind", "?label", "AS ?body", "?sourceKind", "?recordedAt",
                    "?reviewAfter", "?expiresAt", "?status", "?cursor"):
            self.assertIn(col, select)
        self.assertIn('BIND("current" AS ?status)', t)
        self.assertIn('BIND("superseded" AS ?status)', t)
        self.assertNotIn("IF(", t)
        self.assertNotIn("COALESCE", t)
        self.assertIn("FILTER(?cursor > '{since}')", t)
        self.assertTrue(t.endswith("ORDER BY ?cursor LIMIT 51"))
        self.assertIn('CONCAT(STR(?currentAt), "|", STR(?iri)) AS ?cursor', t)
        self.assertIn('CONCAT(STR(?supersededAt), "|", STR(?iri)) AS ?cursor', t)


if __name__ == "__main__":
    unittest.main()
