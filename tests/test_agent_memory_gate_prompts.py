"""The auto-memory gate's replay prompts (docs/design/agent-memory-entities.md
section 6) stay well-formed: 20 prompts, each long enough that bobbin does not
skip it, each naming exactly one expected natural key of a known kind."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "agent-memory" / "gate-prompts.json"
KINDS = {"decision", "directive", "preference", "lesson", "reference",
         "handoff", "incident", "finding", "person"}
BASE = "http://aegis.gastown.local/ontology/mem/"


class GatePrompts(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(FIXTURE.read_text())

    def test_twenty_unique_prompts(self):
        prompts = self.data["prompts"]
        self.assertEqual(len(prompts), 20)
        self.assertEqual(len({p["id"] for p in prompts}), 20)
        self.assertEqual(len({p["prompt"] for p in prompts}), 20)
        self.assertEqual(len({p["expect"]["key"] for p in prompts}), 20)

    def test_each_prompt_clears_bobbin_min_length(self):
        floor = self.data["min_prompt_length"]
        self.assertGreaterEqual(floor, 20)
        for p in self.data["prompts"]:
            with self.subTest(id=p["id"]):
                self.assertGreaterEqual(len(p["prompt"]), floor)

    def test_one_expected_key_of_a_known_kind(self):
        for p in self.data["prompts"]:
            with self.subTest(id=p["id"]):
                exp = p["expect"]
                self.assertEqual(set(exp), {"kind", "key"})
                self.assertIn(exp["kind"], KINDS)
                self.assertTrue(exp["key"].startswith(f"{BASE}{exp['kind']}/"))

    def test_superseded_fixture_targets_a_gate_key(self):
        sup = self.data["superseded_fixture"]
        keys = {p["expect"]["key"]: p["prompt"] for p in self.data["prompts"]}
        self.assertIn(sup["must_not_return"]["key"], keys)
        self.assertEqual(keys[sup["must_not_return"]["key"]], sup["prompt"])


if __name__ == "__main__":
    unittest.main()
