"""aegis-za4f.17.1: aegis:reviewMode on aegis:GitRepo is a closed set, at most one.

The file is the exact bytes of Quipu schema proposal 63 (set
aegis-repo-review-mode); its SHA-256 is pinned here so a Gold declaration can
pin the same bytes. pyshacl validates the four controls; the suite skips them
when pyshacl is absent, as the other shape tests do.
"""
import hashlib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHAPES = ROOT / "shapes/repo-review-mode.shapes.ttl"
PROPOSAL_63_SHA256 = "840e42cf73f32227e34e549ccda8b2834a4076647a1dd88549a3dc3509b210f2"

try:
    import pyshacl
    from rdflib import Graph
except ImportError:  # pragma: no cover
    pyshacl = None

DATA = """@prefix aegis: <http://aegis.gastown.local/ontology/> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
<urn:test:repo> a aegis:GitRepo ; rdfs:label "quipu" {mode} .
"""


class RepoReviewModeShape(unittest.TestCase):
    def test_bytes_are_proposal_63(self) -> None:
        self.assertEqual(hashlib.sha256(SHAPES.read_bytes()).hexdigest(), PROPOSAL_63_SHA256)

    @unittest.skipUnless(pyshacl, "pyshacl not installed")
    def test_closed_set_and_max_one(self) -> None:
        shapes = Graph().parse(SHAPES)
        cases = {
            '; aegis:reviewMode "non-author"': True,
            '; aegis:reviewMode "none"': True,
            "": True,  # absent: the reader's fail-safe default applies, not the shape
            '; aegis:reviewMode "bogus"': False,
            '; aegis:reviewMode "non-author" , "none"': False,
        }
        for mode, expected in cases.items():
            with self.subTest(mode=mode or "absent"):
                data = Graph().parse(data=DATA.format(mode=mode), format="turtle")
                conforms, _, _ = pyshacl.validate(data, shacl_graph=shapes)
                self.assertEqual(conforms, expected)


if __name__ == "__main__":
    unittest.main()
