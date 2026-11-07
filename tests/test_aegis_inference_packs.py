"""Source-only inference placement: each original triple has exactly one owner."""
import hashlib
import json
from pathlib import Path
import unittest

from rdflib import Graph, RDF, SH, URIRef
from rdflib.compare import isomorphic

ROOT = Path(__file__).resolve().parents[1]


class InferencePacks(unittest.TestCase):
    def test_pinned_sources_have_complete_disjoint_subject_assignments(self):
        manifest = json.loads((ROOT/'shapes/aegis/inference-placement.json').read_text())
        self.assertFalse(manifest['automatic_load'])
        self.assertEqual({Path(s['path']).name for s in manifest['sources']},
                         {'aegis-properties.ttl', 'aegis-rules.ttl',
                          'aegis-class-subsumption.rules.ttl'})
        expected = Graph()
        assigned = {}
        for source in manifest['sources']:
            path = ROOT/source['path']
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), source['sha256'])
            graph = Graph().parse(path)
            self.assertTrue(all(isinstance(subject, URIRef) for subject in graph.subjects()))
            subjects = [s for row in source['assignments'] for s in row['subjects']]
            self.assertEqual(len(subjects), len(set(subjects)))
            self.assertEqual(set(subjects), set(map(str, graph.subjects())))
            expected += graph
            for row in source['assignments']:
                part = assigned.setdefault(row['path'], Graph())
                for subject in row['subjects']:
                    for triple in graph.triples((URIRef(subject), None, None)):
                        part.add(triple)
        self.assertEqual(set(assigned), {str(p.relative_to(ROOT)) for p in
                                       (ROOT/'shapes/aegis/inference').glob('*.ttl')})
        actual = Graph()
        for path, expected_part in assigned.items():
            part = Graph().parse(ROOT/path)
            self.assertTrue(isomorphic(part, expected_part), path)
            self.assertFalse(set(actual) & set(part), path)
            actual += part
        self.assertTrue(isomorphic(actual, expected))
        self.assertFalse(isomorphic(Graph(), expected))
        self.assertEqual(list(actual.subjects(RDF.type, SH.NodeShape)), [])
        # A missing domain file must change the union, even though Turtle parses.
        reduced = actual - Graph().parse(ROOT/'shapes/aegis/inference/homelab-infra.rules.ttl')
        self.assertFalse(isomorphic(reduced, expected))

    def test_rule_placement_keeps_cross_domain_and_commit_inference_separate(self):
        base = 'http://aegis.gastown.local/rules/'
        rule = URIRef('http://quipu.local/rule#Rule')
        for domain, names in [('homelab-infra', ['runs_on_transitive']),
                              ('software-factory', ['git_commit_subsumes_commit']),
                              ('shared', ['depends_on_transitive'])]:
            graph = Graph().parse(ROOT/f'shapes/aegis/inference/{domain}.rules.ttl')
            self.assertEqual(set(graph.subjects(RDF.type, rule)), {URIRef(base+n) for n in names})


if __name__ == '__main__':
    unittest.main()
