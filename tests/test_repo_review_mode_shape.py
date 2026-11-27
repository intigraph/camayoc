"""aegis-za4f.17.1: aegis:reviewMode on aegis:GitRepo is a closed set, at most one.

The file is the exact bytes of Quipu schema proposal 63 (set
aegis-repo-review-mode); its SHA-256 is pinned here so a Gold declaration can
pin the same bytes. pyshacl validates the four controls; the suite skips them
when pyshacl is absent, as the other shape tests do.
"""
import hashlib
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHAPES = ROOT / "shapes/repo-review-mode.shapes.ttl"
QUERY = ROOT / "queries/repo_review_mode.json"
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


GRAPH = """@prefix aegis: <http://aegis.gastown.local/ontology/> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
aegis:repo_quipu a aegis:GitRepo ; rdfs:label "repo_quipu" ; skos:altLabel "quipu" , "repo_quipu" ;
    aegis:reviewMode "non-author" .
aegis:quipu a aegis:GitRepo ; rdfs:label "quipu" ; aegis:reviewMode "non-author" .
aegis:repo_cv a aegis:GitRepo ; rdfs:label "repo_cv" ; aegis:reviewMode "none" .
aegis:split a aegis:GitRepo ; rdfs:label "split" ; aegis:reviewMode "none" .
aegis:split2 a aegis:GitRepo ; skos:altLabel "split" ; aegis:reviewMode "non-author" .
aegis:unset a aegis:GitRepo ; rdfs:label "unset" .
"""


class RepoReviewModeQuery(unittest.TestCase):
    """The stored query text, run by rdflib with Quipu's '{repo}' substitution."""

    @classmethod
    def setUpClass(cls) -> None:
        from rdflib import Graph as G

        cls.definition = json.loads(QUERY.read_text())
        cls.graph = G().parse(data=GRAPH, format="turtle")

    def rows(self, repo: str) -> set:
        text = self.definition["template"].replace("{repo}", repo)
        return {(str(n).rsplit("/", 1)[-1], str(m)) for n, m in self.graph.query(text)}

    def test_definition_shape(self) -> None:
        self.assertEqual(self.definition["name"], "repo_review_mode")
        self.assertEqual([p["name"] for p in self.definition["params"]], ["repo"])
        self.assertIn("LIMIT 64", self.definition["template"])

    def test_owner_slash_name_and_bare_label_match_every_alias_once(self) -> None:
        expected = {("repo_quipu", "non-author"), ("quipu", "non-author")}
        self.assertEqual(self.rows("intigraph/quipu"), expected)
        self.assertEqual(self.rows("quipu"), expected)

    def test_none_and_unknown_and_unset(self) -> None:
        self.assertEqual(self.rows("repo_cv"), {("repo_cv", "none")})
        self.assertEqual(self.rows("stiwi/nonexistent"), set())
        self.assertEqual(self.rows("unset"), set())

    def test_conflicting_aliases_return_both_rows(self) -> None:
        self.assertEqual(self.rows("split"), {("split", "none"), ("split2", "non-author")})


if __name__ == "__main__":
    unittest.main()
