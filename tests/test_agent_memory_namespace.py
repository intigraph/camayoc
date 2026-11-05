"""Memory naming retains the shared ledger and scoped validation contract."""
import json
from pathlib import Path
import sys
import unittest
from rdflib import Graph, OWL, RDF, RDFS, SH, URIRef
from rdflib.collection import Collection

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from build_stable_namespaces import combined_ledger, agent_memory_ledger

A = 'http://aegis.gastown.local/ontology/'
C = 'https://w3id.org/intigraph/camayoc/core/ns#'
Q = 'https://w3id.org/intigraph/quipu/ns#'


class MemoryNaming(unittest.TestCase):
    def setUp(self):
        self.ledger = combined_ledger(ROOT)
        self.mapping = {r['old']: r['new'] for r in self.ledger['pairs']}

    def proposal(self, name):
        body = json.loads((ROOT/'docs/design/proposals/agent-memory'/name).read_text())
        return Graph().parse(data=body['diff'], format='turtle')

    def test_existing_dispositions_win_over_memory_pack_default(self):
        self.assertEqual(self.mapping[A+'Decision'], Q+'Decision')
        self.assertEqual(self.mapping[A+'issuedBy'], Q+'issuedBy')
        self.assertEqual(self.mapping[A+'Directive'], C+'Directive')
        self.assertEqual(set(self.ledger['retained_legacy']), {A+'assignedTo', A+'closedAt'})
        for old in self.ledger['agent_memory']:
            self.assertEqual(self.mapping[old], C+old.removeprefix(A))
        existing = [r for r in self.ledger['pairs'] if r['old'] not in self.ledger['agent_memory']]
        with self.assertRaisesRegex(ValueError, 'collides'):
            agent_memory_ledger(ROOT, self.ledger['pairs'])
        self.assertEqual(len(agent_memory_ledger(ROOT, existing)), 37)

    def test_vocabulary_bridges_properties_and_directives_without_domains(self):
        g = self.proposal('01-ontology.json')
        self.assertFalse(list(g.triples((None, RDFS.domain, None))))
        # Existing predicates are not reclassified as datatype properties.
        self.assertNotIn((URIRef(C+'about'), RDF.type, OWL.DatatypeProperty), g)
        for old, new in [(A+'naturalKey', C+'naturalKey'), (A+'issuedBy', Q+'issuedBy')]:
            self.assertIn((URIRef(old), OWL.sameAs, URIRef(new)), g)
            self.assertIn((URIRef(old), OWL.equivalentProperty, URIRef(new)), g)
            self.assertIn((URIRef(new), RDF.type, RDF.Property), g)
        self.assertIn((URIRef(A+'Directive'), OWL.equivalentClass, URIRef(C+'Directive')), g)

    def test_scoped_target_covers_both_spellings_and_never_decisions(self):
        g = self.proposal('02-shapes-new-classes.json') + self.proposal('03-shape-directive-scoped.json')
        target = URIRef(C+'MemoryDirectiveShape')
        self.assertEqual(set(g.objects(target, SH.targetSubjectsOf)),
                         {URIRef(A+'naturalKey'), URIRef(C+'naturalKey')})
        self.assertFalse(list(g.objects(target, SH.targetClass)))
        self.assertFalse(list(g.triples((None, SH.targetClass, URIRef(Q+'Decision')))))
        self.assertFalse(list(g.triples((None, SH.targetClass, URIRef(A+'Decision')))))
        published = Graph().parse(ROOT/'shapes/agent-memory-scoped.shapes.ttl')
        self.assertFalse(list(published.triples((None, SH.targetClass, URIRef(A+'Decision')))))
        # Every cardinality guard has a literal predicate the write planner
        # can verify; required/type constraints live in per-field branches.
        self.assertFalse(list(g.triples((None, SH.alternativePath, None))))
        for prop in g.subjects(SH.maxCount, None):
            self.assertIsInstance(g.value(prop, SH.path), URIRef)
        paths = set(g.objects(None, SH.path))
        self.assertTrue({URIRef(A+'rule'), URIRef(C+'rule'),
                         URIRef(A+'issuedBy'), URIRef(Q+'issuedBy')} <= paths)
        # Every named profile is stable; legacy IRIs occur only as compatibility references.
        for shape in g.subjects(RDF.type, SH.NodeShape):
            self.assertTrue(str(shape).startswith(C), shape)


if __name__ == '__main__':
    unittest.main()
