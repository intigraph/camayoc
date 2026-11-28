"""aegis-b9k: a Directive carries at most one typed, zoned traceability deadline.

Standalone REJECT shape set (sattler ruling 2026-11-28). The bytes are pinned so
the Quipu proposal and the Gold declaration pin the same bytes. The pyshacl
controls run when pyshacl is installed, as the other shape tests do.
"""
import datetime as dt
import hashlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHAPES = ROOT / "shapes/directive-deadline.shapes.ttl"
sys.path.insert(0, str(ROOT / "scripts"))
import directive_deadline as dd  # noqa: E402

SHAPES_SHA256 = "f69a1a9c9691f5fab7ab99877eeda989c7b00e421098068de966ef13b765e9b7"

try:
    from rdflib import Graph
    import pyshacl
except ImportError:  # pragma: no cover
    pyshacl = None

DATA = """@prefix a: <http://aegis.gastown.local/ontology/> .
@prefix q: <https://w3id.org/intigraph/quechua/ns#> .
@prefix old: <https://scbrown.github.io/quechua/ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
a:d a a:Directive ; rdfs:label "Decision" {due} .
a:p a a:Policy ; q:traceabilityDueAt "x" , "y" .
"""
D1 = '"2030-12-05T00:00:00Z"^^xsd:dateTime'
D2 = '"2030-12-06T00:00:00+01:00"^^xsd:dateTime'


class DirectiveDeadlineShape(unittest.TestCase):
    def test_bytes_are_pinned(self) -> None:
        self.assertEqual(hashlib.sha256(SHAPES.read_bytes()).hexdigest(), SHAPES_SHA256)

    @unittest.skipUnless(pyshacl, "pyshacl not installed")
    def test_standalone_reject_only_about_the_deadline(self) -> None:
        g = Graph().parse(SHAPES)
        preds = {str(p) for p in g.predicates()}
        for absent in ("http://quipu.dev/ontology/onViolation", "http://quipu.dev/ns#onViolation",
                       "http://www.w3.org/ns/shacl#severity",  # default sh:Violation, default REJECT
                       "http://www.w3.org/ns/shacl#minCount", "http://www.w3.org/2000/01/rdf-schema#domain"):
            self.assertNotIn(absent, preds)
        shapes = set(g.subjects(predicate=None, object=Graph().namespace_manager.expand_curie("sh:NodeShape")))
        self.assertEqual({str(s) for s in shapes},
                         {"http://aegis.gastown.local/ontology/DirectiveDeadlineCardinalityShape"})

    @unittest.skipUnless(pyshacl, "pyshacl not installed")
    def test_one_typed_zoned_deadline_or_none(self) -> None:
        shapes = Graph().parse(SHAPES)
        cases = {
            "": True,  # untraced and undated: NOT this shape's concern (capture path)
            f"; q:traceabilityDueAt {D1}": True,
            f"; old:traceabilityDueAt {D2}": True,
            f"; q:traceabilityDueAt {D1} , {D2}": False,  # the b9k case
            f"; q:traceabilityDueAt {D1} ; old:traceabilityDueAt {D2}": False,
            '; q:traceabilityDueAt "2030-12-05T00:00:00Z"': False,  # untyped
            '; q:traceabilityDueAt "2030-12-05T00:00:00"^^xsd:dateTime': False,  # no zone
        }
        for due, expected in cases.items():
            with self.subTest(due=due or "absent"):
                data = Graph().parse(data=DATA.format(due=due), format="turtle")
                conforms, _, report = pyshacl.validate(data, shacl_graph=shapes)
                self.assertEqual(conforms, expected, report)


class CrossGraphConflictIsOutsideTheShape(unittest.TestCase):
    """Review finding 3 on camayoc#298: the reader unions ROOT and the declared
    graph, so two graphs each holding ONE deadline still conflict. A per-write
    shape sees one graph's write at a time and cannot refuse that; the adapter's
    per-item isolation (camayoc#299) is what carries it."""

    PER_GRAPH = {dd.ROOT_GRAPH: "2030-12-05T00:00:00Z",
                 "urn:quipu:graph:declared": "2030-12-06T00:00:00+01:00"}

    def test_reader_refuses_the_union_of_two_single_deadlines(self) -> None:
        def rows(_query, graph):
            return [{"due": self.PER_GRAPH[graph], "datatype": dd.XSD_DATETIME}]
        with self.assertRaisesRegex(ValueError, "conflicting traceability deadlines"):
            dd.deadline(rows, "http://aegis.gastown.local/ontology/d",
                        "urn:quipu:graph:declared", dt.datetime.fromisoformat)

    @unittest.skipUnless(pyshacl, "pyshacl not installed")
    def test_each_graph_alone_conforms(self) -> None:
        shapes = Graph().parse(SHAPES)
        for graph, value in self.PER_GRAPH.items():
            with self.subTest(graph=graph):
                due = f'; q:traceabilityDueAt "{value}"^^xsd:dateTime'
                data = Graph().parse(data=DATA.format(due=due), format="turtle")
                conforms, _, report = pyshacl.validate(data, shacl_graph=shapes)
                self.assertTrue(conforms, report)


if __name__ == "__main__":
    unittest.main()
