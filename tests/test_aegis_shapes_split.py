"""The split-plan domain table is an exact, counted partition of the upstream pin.

docs/design/aegis-shapes-split.md §3 is the record a later slice verifies the
move against. If the table drops, duplicates or miscounts a shape, or the
upstream pin moves, this fails before anyone builds files from a wrong plan.
"""
import collections
import hashlib
import json
from pathlib import Path
import re
import unittest

from rdflib import Graph, RDF, SH, URIRef

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / 'docs/design/aegis-shapes-split.md'
UPSTREAM = ROOT / 'tests/fixtures/aegis-upstream/original-aegis-ontology.ttl'
A = 'http://aegis.gastown.local/ontology/'
PIN = '9f1c93625b5b857638287548565b29f132f51da7d2bc4b27b03406cba556d319'


def table_rows(text):
    body = text.split('<!-- split-table:begin -->')[1].split('<!-- split-table:end -->')[0]
    rows = []
    for line in body.strip().splitlines()[2:]:
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        domain, _file, shapes, classes, _staged, names = cells
        rows.append((domain, int(shapes), int(classes), [n.strip() + 'Shape' for n in names.split(',')]))
    return rows


def partition_errors(rows, graph):
    named = {str(s)[len(A):] for t in (SH.NodeShape, SH.PropertyShape)
             for s in graph.subjects(RDF.type, t) if isinstance(s, URIRef)}
    seen = collections.Counter(n for r in rows for n in r[3])
    errors = []
    if set(seen) != named:
        errors.append(('coverage', sorted(named - set(seen)), sorted(set(seen) - named)))
    errors += [('duplicate', n) for n, c in seen.items() if c > 1]
    for domain, shapes, classes, names in rows:
        targets = {c for n in names for c in graph.objects(URIRef(A + n), SH.targetClass)}
        if shapes != len(names) or classes != len(targets):
            errors.append(('count', domain))
    return errors


class SplitPlan(unittest.TestCase):
    def setUp(self):
        self.rows = table_rows(DOC.read_text())
        self.graph = Graph().parse(UPSTREAM)

    def test_upstream_pin_is_the_staged_one(self):
        self.assertEqual(hashlib.sha256(UPSTREAM.read_bytes()).hexdigest(), PIN)
        manifest = json.loads((ROOT / 'shapes/aegis/placement.json').read_text())
        self.assertEqual(manifest['source_sha256'], PIN)

    def test_table_is_an_exact_counted_partition(self):
        self.assertEqual(partition_errors(self.rows, self.graph), [])
        self.assertEqual(sum(r[1] for r in self.rows), 124)
        self.assertEqual(sum(r[2] for r in self.rows), 98)

    def test_only_the_code_referenced_shape_is_engine(self):
        engine = [r for r in self.rows if r[0] == 'ENGINE']
        self.assertEqual([r[3] for r in engine], [['InternalIdentifierPatternShape']])

    def test_the_check_can_fail(self):
        dropped = [(d, s, c, n[1:] if d == 'crew' else n) for d, s, c, n in self.rows]
        self.assertTrue(any(e[0] == 'coverage' for e in partition_errors(dropped, self.graph)))
        doubled = [(d, s, c, n + ['RigShape'] if d == 'alerts' else n) for d, s, c, n in self.rows]
        kinds = {e[0] for e in partition_errors(doubled, self.graph)}
        self.assertEqual(kinds, {'duplicate', 'count'})


if __name__ == '__main__':
    unittest.main()
