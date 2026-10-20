"""Lossless staged placement, public retargeting, and every-class CQ controls."""
import hashlib
import json
from pathlib import Path
import unittest

from rdflib import Graph, RDF, SH, URIRef, Literal, RDFS
from rdflib.compare import isomorphic

ROOT = Path(__file__).resolve().parents[1]
PACKS = ROOT / 'shapes/aegis'


class DomainPacks(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((PACKS/'placement.json').read_text())
        self.legacy = Graph().parse(ROOT/'tests/fixtures/aegis-upstream/legacy-compatibility.shapes.ttl')

    def test_compatibility_bytes_and_total_assignment(self):
        self.assertEqual(hashlib.sha256((ROOT/'tests/fixtures/aegis-upstream/legacy-compatibility.shapes.ttl').read_bytes()).hexdigest(),
                         self.manifest['source_sha256'])
        source = {str(s) for s in self.legacy.subjects(RDF.type, SH.NodeShape)}
        assigned = [row['shape'] for row in self.manifest['shapes']]
        self.assertEqual(len(assigned), len(set(assigned)))
        self.assertEqual(set(assigned), source)
        self.assertFalse(self.manifest['automatic_load'])

    def test_recombination_preserves_every_original_triple(self):
        combined = Graph()
        for file in sorted({r['file'] for r in self.manifest['shapes']}):
            combined += Graph().parse(ROOT/file)
        for row in self.manifest['shapes']:
            for old, new in row['public_target_substitutions'].items():
                combined.remove((URIRef(row['shape']), SH.targetClass, URIRef(new)))
                combined.add((URIRef(row['shape']), SH.targetClass, URIRef(old)))
        combined += Graph().parse(PACKS/'shared-vocabulary.ttl')
        self.assertTrue(isomorphic(combined, self.legacy))
        self.assertFalse(isomorphic(Graph(), self.legacy))  # populated target control

    def test_drop_targets_are_public_and_infra_is_not_recast_as_quechua(self):
        for row in self.manifest['shapes']:
            pack = Graph().parse(ROOT/row['file'])
            targets = {str(t) for t in pack.objects(URIRef(row['shape']), SH.targetClass)}
            if row['trim_decisions'] and set(row['trim_decisions']) == {'DROP'}:
                self.assertEqual(targets, set(row['public_target_substitutions'].values()))
            else:
                self.assertEqual(targets, set(row['source_targets']))

    def test_each_class_question_executes_positive_and_negative(self):
        questions = json.loads((ROOT/'competency/aegis-domain-classes.json').read_text())['queries']
        source = {str(t) for t in self.legacy.objects(None, SH.targetClass)}
        self.assertEqual({q['class'] for q in questions}, source)
        for question in questions:
            with self.subTest(target=question['class']):
                graph = Graph()
                entity = URIRef('https://example.org/fixture')
                graph.add((entity, RDF.type, URIRef(question['effective_target'])))
                self.assertEqual(len(list(graph.query(question['query']))), 0)
                graph.add((entity, RDFS.label, Literal('fixture')))
                self.assertEqual([row[0] for row in graph.query(question['query'])], [entity])

    def test_seeds_owns_work_profile_and_calendar_is_not_identity(self):
        core = Graph().parse(ROOT/'shapes/core.shapes.ttl')
        legacy = URIRef('http://aegis.gastown.local/ontology/WorkItem')
        self.assertEqual(list(core.subjects(SH.targetClass, legacy)), [])
        contract = json.loads((PACKS/'seeds-profile.json').read_text())
        self.assertEqual(contract['canonical_type'], 'https://schema.org/Action')
        self.assertEqual(contract['calendar_relation'], 'http://www.w3.org/2004/02/skos/core#exactMatch')
        self.assertFalse(contract['vendored'])
        ontology = Graph().parse(ROOT/'ontology/core.ttl')
        self.assertIn((legacy, URIRef('http://purl.org/dc/terms/isReplacedBy'),
                       URIRef('https://schema.org/Action')), ontology)


if __name__ == '__main__': unittest.main()
