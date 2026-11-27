"""aegis-za4f.17.1: aegis:reviewMode on aegis:GitRepo is a closed set, at most one.

The file is the exact bytes to re-propose as Quipu shape set
aegis-repo-review-mode, superseding pending proposal 63, whose bytes carried an
rdfs:domain (CLAUDE.md: rdfs:range only, never rdfs:domain on shared
predicates). Its SHA-256 is pinned here so the proposal and a Gold declaration
can pin the same bytes. pyshacl validates the four controls; the suite skips them
when pyshacl is absent, as the other shape tests do.
"""
import hashlib
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHAPES = ROOT / "shapes/repo-review-mode.shapes.ttl"
QUERY = ROOT / "queries/repo_review_mode.json"
SHAPES_SHA256 = "2116f5e66cce211c4d69a33740d2359eb02bb264858b6090e888c7b2a66edc4c"

try:
    from rdflib import Graph
except ImportError:  # pragma: no cover
    Graph = None
try:
    import pyshacl
except ImportError:  # pragma: no cover
    pyshacl = None

DATA = """@prefix aegis: <http://aegis.gastown.local/ontology/> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
<urn:test:repo> a aegis:GitRepo ; rdfs:label "quipu" {mode} .
"""


class RepoReviewModeShape(unittest.TestCase):
    def test_bytes_are_pinned(self) -> None:
        self.assertEqual(hashlib.sha256(SHAPES.read_bytes()).hexdigest(), SHAPES_SHA256)

    def test_no_rdfs_domain(self) -> None:
        self.assertNotIn("rdfs:domain aegis:", SHAPES.read_text())

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
aegis:repo_quipu a aegis:GitRepo ; rdfs:label "repo_quipu" ; skos:altLabel "quipu" , "intigraph/quipu" ;
    aegis:reviewMode "non-author" .
aegis:quipu a aegis:GitRepo ; rdfs:label "quipu" ; aegis:reviewMode "non-author" .
aegis:repo_cv a aegis:GitRepo ; rdfs:label "repo_cv" ; skos:altLabel "stiwi/cv" ; aegis:reviewMode "none" .
aegis:stiwi_foo a aegis:GitRepo ; rdfs:label "foo" ; skos:altLabel "stiwi/foo" ; aegis:reviewMode "none" .
aegis:intigraph_foo a aegis:GitRepo ; rdfs:label "foo" ; skos:altLabel "intigraph/foo" .
aegis:split a aegis:GitRepo ; skos:altLabel "x/split" ; aegis:reviewMode "none" .
aegis:split2 a aegis:GitRepo ; skos:altLabel "x/split" ; aegis:reviewMode "non-author" .
"""


def quipu_text(raw: str) -> str:
    """Quipu's own text-param escaping (src/mcp/named_query.rs ParamKind::Text)."""
    return raw.replace("\\", "\\\\").replace("'", "\\'")


@unittest.skipUnless(Graph, "rdflib not installed")
class RepoReviewModeQuery(unittest.TestCase):
    """The stored query text, run by rdflib with Quipu's escaped '{repo}' substitution."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.definition = json.loads(QUERY.read_text())
        cls.graph = Graph().parse(data=GRAPH, format="turtle")

    def rows(self, repo: str) -> set:
        text = self.definition["template"].replace("{repo}", quipu_text(repo))
        return {(str(n).rsplit("/", 1)[-1], str(m)) for n, m in self.graph.query(text)}

    def test_definition_shape(self) -> None:
        self.assertEqual(self.definition["name"], "repo_review_mode")
        self.assertEqual(self.definition["params"], [{
            "name": "repo", "type": "text", "required": True,
            "description": "Repository as 'owner/name' (e.g. 'intigraph/quipu')."}])
        self.assertIn("LIMIT 64", self.definition["template"])
        self.assertTrue(QUERY.read_text().endswith("\n"))

    def test_owner_qualified_match(self) -> None:
        self.assertEqual(self.rows("intigraph/quipu"), {("repo_quipu", "non-author")})
        self.assertEqual(self.rows("stiwi/cv"), {("repo_cv", "none")})

    def test_another_owners_none_never_answers(self) -> None:
        # Reviewer's case: none on stiwi/foo, unset on intigraph/foo -> no rows (non-author).
        self.assertEqual(self.rows("intigraph/foo"), set())
        self.assertEqual(self.rows("stiwi/foo"), {("stiwi_foo", "none")})

    def test_unknown_returns_no_rows(self) -> None:
        self.assertEqual(self.rows("stiwi/nonexistent"), set())

    def test_conflicting_nodes_return_both_rows(self) -> None:
        self.assertEqual(self.rows("x/split"), {("split", "none"), ("split2", "non-author")})

    def test_quote_cannot_rewrite_the_query(self) -> None:
        # Unescaped, this would close the literal and add 'stiwi/cv' as a second key.
        self.assertEqual(self.rows("x' 'stiwi/cv"), set())


if __name__ == "__main__":
    unittest.main()
