"""Admission/refusal controls against the actual composed release aggregate."""
import importlib.util
from pathlib import Path
import unittest

from rdflib import Graph, Namespace, RDF
from pyshacl import validate

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('deadline_bundle', ROOT / 'scripts/build_aegis_compatibility.py')
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)

class DirectiveDeadlines(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.artifacts = BUILDER.compatibility_bundle(
            ROOT, ROOT / 'tests/fixtures/aegis-upstream/legacy-compatibility.shapes.ttl')
        cls.shapes = Graph()
        for name, data in cls.artifacts.items():
            if name.endswith('.ttl'):
                cls.shapes += Graph().parse(data=data, format='turtle')

    def fixture(self, deadline):
        prefix = '''@prefix a:<http://aegis.gastown.local/ontology/>.
@prefix q:<https://w3id.org/intigraph/quechua/ns#>.
@prefix old:<https://scbrown.github.io/quechua/ns#>.
@prefix rdfs:<http://www.w3.org/2000/01/rdf-schema#>.
@prefix xsd:<http://www.w3.org/2001/XMLSchema#>.
'''
        return Graph().parse(data=prefix + '\n'.join(
            f'a:d{i} a a:Directive;rdfs:label "Decision {i}";a:issuedBy "Operator" {deadline} .'
            for i in range(3)), format='turtle')

    def test_three_directives_with_either_owned_date_predicate_are_admitted(self):
        for predicate in ('q:traceabilityDueAt', 'old:traceabilityDueAt'):
            with self.subTest(predicate=predicate):
                conforms, _, report = validate(self.fixture(
                    f';{predicate} "2030-12-05T00:00:00Z"^^xsd:dateTime'),
                    shacl_graph=self.shapes, advanced=True)
                self.assertTrue(conforms, report)

    def test_missing_or_malformed_dates_block(self):
        for deadline in ('', ';q:traceabilityDueAt "2030-12-05T00:00:00Z"',
                         ';a:traceabilityDueAt "2030-12-05T00:00:00Z"^^xsd:dateTime',
                         ';q:traceabilityDueAt "2030-12-05T00:00:00"^^xsd:dateTime',
                         ';q:traceabilityDueAt "2030-12-05T00:00:00Z"^^xsd:dateTime, "2030-12-06T00:00:00Z"^^xsd:dateTime'):
            with self.subTest(deadline=deadline):
                conforms, results, report = validate(self.fixture(deadline), shacl_graph=self.shapes, advanced=True)
                self.assertFalse(conforms, report)
                sh = Namespace('http://www.w3.org/ns/shacl#')
                self.assertTrue(any(results.objects(None, sh.resultSeverity)), report)
                self.assertIn(sh.Violation, set(results.objects(None, sh.resultSeverity)))

if __name__ == '__main__':
    unittest.main()
