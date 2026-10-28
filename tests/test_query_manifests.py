"""Stored-query manifests must match Quipu's released parameter schema."""

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class StoredQueryManifestTests(unittest.TestCase):
    def test_every_parameter_is_a_typed_object_and_covers_the_template(self):
        for path in sorted((ROOT / "queries").glob("*.json")):
            with self.subTest(query=path.name):
                manifest = json.loads(path.read_text())
                params = manifest.get("params", [])
                self.assertTrue(all(isinstance(param, dict) for param in params))
                for param in params:
                    self.assertIn(param["type"], {"iri", "text"})
                    self.assertTrue(param["description"])
                    if param["required"] is True:
                        self.assertEqual({"name", "type", "required", "description"}, set(param))
                    else:
                        # Quipu refuses an optional param without a default: an omitted
                        # optional would leave `{name}` verbatim in the rendered SPARQL.
                        self.assertIs(param["required"], False)
                        self.assertEqual({"name", "type", "required", "default", "description"}, set(param))
                        self.assertIsInstance(param["default"], str)

                declared = {param["name"] for param in params}
                referenced = set(re.findall(r"\{([A-Za-z][A-Za-z0-9_]*)\}", manifest["template"]))
                self.assertEqual(referenced, declared)


if __name__ == "__main__":
    unittest.main()
