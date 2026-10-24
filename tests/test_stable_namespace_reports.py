"""Validation reports agree, old vs stable, once mapped back (aegis-m5s.1.1).

Shape IRIs are visible in validation reports, so a renamed shape is a reader-facing
change. These controls run the real `quipu validate` on the same fixture twice:
legacy shapes over legacy data, and the stable pack over the remapped data. The
two reports must be identical after the stable one is mapped back through the
inverse ledger, and the stable one must actually name the NEW shape IRIs.
"""
import collections
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from rdflib import BNode, Graph, Literal, Namespace, RDF, RDFS, URIRef

from quipu_bin_guard import QUIPU_REPORT, requires_quipu_report

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from build_stable_namespaces import combined_ledger, remap_graph, stable_bundle

A = Namespace('http://aegis.gastown.local/ontology/')
EX = Namespace('https://example.org/namespace-report/')


def change_event(graph, name, action, sha=None):
    node = EX[name]
    graph.add((node, RDF.type, A.ChangeEvent))
    graph.add((node, A.sourceKind, Literal('observed')))
    graph.add((node, RDFS.label, Literal(name)))
    graph.add((node, A.changeId, Literal('owner/repo#1')))
    graph.add((node, A.changeRepository, Literal('owner/repo')))
    graph.add((node, A.changeAction, Literal(action)))
    graph.add((node, A.occurredAt, Literal('2026-10-24T00:00:00Z')))
    graph.add((node, A.cdeventsType, Literal('dev.cdevents.change.' + action)))
    graph.add((node, A.cloudEventSource, Literal('fixture')))
    graph.add((node, A.cloudEventId, Literal(name)))
    if sha:
        graph.add((node, A.commitSha, Literal(sha)))


def fixtures():
    delivery = Graph()
    change_event(delivery, 'created-ok', 'created')
    change_event(delivery, 'merged-ok', 'merged', '0' * 40)
    # A merge with no commit violates the node-level sh:or on ChangeEventShape,
    # so its report names that NodeShape directly.
    change_event(delivery, 'merged-missing-sha', 'merged')
    code = Graph()
    commit = EX['commit-missing-metadata']
    code.add((commit, RDF.type, A.Commit))
    code.add((commit, RDFS.label, Literal('commit without sha or repo')))
    return {'delivery': delivery, 'code-entities': code}


def results(shapes, data):
    with tempfile.TemporaryDirectory() as tmp:
        shapes_path, data_path = Path(tmp) / 'shapes.ttl', Path(tmp) / 'data.ttl'
        shapes_path.write_bytes(shapes)
        data_path.write_text(data.serialize(format='turtle'))
        run = subprocess.run([str(QUIPU_REPORT), 'validate', '--shapes', str(shapes_path),
                              '--data', str(data_path), '--format', 'json'],
                             capture_output=True, text=True, timeout=60)
    if not run.stdout.lstrip().startswith('{'):
        raise AssertionError('quipu validate gave no JSON report; it needs a release with '
                             f'--format json (0.16+): {run.stdout[:200]!r} {run.stderr[:200]!r}')
    return json.loads(run.stdout)


def key(issue, inverse):
    def back(term):
        if term is None:
            return None
        bare = term[1:-1] if term.startswith('<') and term.endswith('>') else term
        # Blank-node labels are per-parse; only named shapes are comparable.
        return '_:blank' if bare.startswith('_:') else inverse.get(bare, bare)
    return (issue['severity'], back(issue['focus_node']), issue['component'],
            back(issue.get('path')), issue.get('value'), back(issue.get('source_shape')))


@requires_quipu_report
class StableNamespaceReports(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ledger = combined_ledger(ROOT)
        cls.mapping = {row['old']: row['new'] for row in ledger['pairs']}
        cls.inverse = {new: old for old, new in cls.mapping.items()}
        cls.artifacts = stable_bundle(ROOT)

    def compare(self, pack, source):
        data = fixtures()[pack]
        legacy = results((ROOT / source).read_bytes(), data)
        stable = results(self.artifacts[f'camayoc/{pack}/ns.ttl'], remap_graph(data, self.mapping))
        for field in ('conforms', 'violations', 'warnings'):
            self.assertEqual(legacy[field], stable[field], (pack, field))
        self.assertEqual(collections.Counter(key(r, {}) for r in legacy['results']),
                         collections.Counter(key(r, self.inverse) for r in stable['results']), pack)
        return legacy, stable

    def test_delivery_report_names_the_renamed_change_event_shape(self):
        legacy, stable = self.compare('delivery', 'shapes/delivery.shapes.ttl')
        self.assertFalse(legacy['conforms'])
        old = str(A.ChangeEventShape)
        new = self.mapping[old]
        shapes = lambda report: {r.get('source_shape', '').strip('<>') for r in report['results']}
        self.assertIn(old, shapes(legacy))
        self.assertIn(new, shapes(stable))
        self.assertNotIn(old, shapes(stable))
        focus = lambda report: {r['focus_node'].strip('<>') for r in report['results']}
        # Positive control: only the defective merge is reported.
        self.assertEqual(focus(legacy), {str(EX['merged-missing-sha'])})

    def test_code_entity_debt_stays_a_warning_after_the_rename(self):
        legacy, stable = self.compare('code-entities', 'shapes/code-entities.ttl')
        severities = collections.Counter(r['severity'] for r in stable['results'])
        # The debt shapes emit warnings; the rename must not turn them into violations.
        self.assertGreater(severities['Warning'], 0, stable)
        debt = [r for r in stable['results'] if r['focus_node'].strip('<>') ==
                str(EX['commit-missing-metadata']) and r.get('path', '').strip('<>') in
                {self.mapping.get(str(A.sha), str(A.sha)),
                 self.mapping.get(str(A.in_repo), str(A.in_repo))}]
        self.assertTrue(debt, stable)
        self.assertEqual({r['severity'] for r in debt}, {'Warning'})


if __name__ == '__main__':
    unittest.main()
