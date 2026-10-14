"""Complete portable declaration/profile coverage; no runtime activation."""
import json
from pathlib import Path
import unittest
from rdflib import Graph, Namespace, RDF, RDFS, SH

ROOT = Path(__file__).resolve().parents[1] / "governance"
Q = Namespace("https://scbrown.github.io/quechua/ns#")
CLASSES = ("Directive", "Policy", "TextRule", "InternalIdentifierPattern",
           "OperatingPoint", "Reaction", "AdmissionPolicy", "Decision", "DecisionRecord")

class ResourceTests(unittest.TestCase):
    def test_all_requested_classes_have_direct_shapes(self):
        catalog = Graph().parse(ROOT / "catalog.ttl", format="turtle")
        shapes = Graph().parse(ROOT / "shapes.ttl", format="turtle")
        for name in CLASSES:
            with self.subTest(name=name):
                self.assertIn((Q[name], RDF.type, RDFS.Class), catalog)
                self.assertTrue(list(shapes.subjects(SH.targetClass, Q[name])))
        self.assertEqual(len(catalog), 1418)
        self.assertEqual(len(shapes), 2133)

    def test_manifest_contains_only_declaration_resource(self):
        manifest = json.loads((ROOT / "manifest.json").read_text())
        counts = json.loads(manifest["counts"])
        self.assertEqual((counts["facts"], counts["shapes"]), (1418, 1))
        self.assertEqual((counts["queries"], counts["vectors"]), (0, 0))
        self.assertEqual(manifest["version"], "0.7.0")

if __name__ == "__main__":
    unittest.main()
