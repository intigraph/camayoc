"""Execute the actual stored query against a private RDF dataset."""
import json
import unittest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
try:
    from rdflib import Graph, Dataset, Namespace, RDF, RDFS, Literal, XSD, URIRef
except ImportError:
    Dataset = None


@unittest.skipIf(Dataset is None, 'rdflib needed for private query qualification')
class OverdueQuery(unittest.TestCase):
    def test_past_due_untraced_only_with_typed_links_and_trusted_scope(self):
        root = Path(__file__).resolve().parents[1]
        record = json.loads((root/'queries/camayoc_overdue_directive_traceability.json').read_text())
        ds = Dataset()
        g = ds.graph(URIRef('urn:quipu:graph:root'))
        declared = 'urn:test:declared'
        trusted = ds.graph(URIRef(declared))
        a = Namespace('http://aegis.gastown.local/ontology/')
        q = Namespace('https://w3id.org/intigraph/quechua/ns#')
        old = Namespace('https://scbrown.github.io/quechua/ns#')
        for name, date in [('overdue','2020-01-01T00:00:00Z'), ('future','2030-01-01T00:00:00Z'),
                           ('traced','2020-01-01T00:00:00Z'), ('untyped-links','2020-01-01T00:00:00Z'),
                           ('legacy','2020-01-01T00:00:00Z'), ('inferred-only','2020-01-01T00:00:00Z')]:
            g.add((a[name], RDF.type, a.Directive))
            g.add((a[name], RDFS.label, Literal(name)))
            g.add((a[name], old.traceabilityDueAt if name=='legacy' else q.traceabilityDueAt,
                   Literal(date, datatype=XSD.dateTime)))
        for name in ('traced', 'untyped-links', 'inferred-only'):
            target = ds.graph(URIRef('urn:test:inferred')) if name=='inferred-only' else trusted
            target.add((a[name], a.trackedBy, a[name+'-work']))
            target.add((a[name], a.governedBy, a[name+'-policy']))
            if name != 'untyped-links':
                target.add((a[name+'-work'], RDF.type, a.WorkItem))
                target.add((a[name+'-policy'], RDF.type, a.Policy))
        query = record['template'].replace('{declared}',declared).replace('{now}','2026-11-14T08:00:00Z')
        rows = list(ds.query(query))
        self.assertEqual({str(r.directive) for r in rows}, {str(a[x]) for x in ('overdue','untyped-links','legacy','inferred-only')})

    def test_lapse_consumer_requires_reference_types_and_trusted_deadline_scope(self):
        import directive_edges as de
        import datetime as dt
        a = Namespace('http://aegis.gastown.local/ontology/')
        q = Namespace('https://w3id.org/intigraph/quechua/ns#')
        graphs = {None: Graph(), de.DECLARED: Graph(),
                  de.INFERRED: Graph()}
        graph = graphs[None]
        graphs[de.ROOT_GRAPH] = graph
        graphs[de.INFERRED].add((a.rule, q.traceabilityDueAt, Literal("2030-01-01T00:00:00Z", datatype=XSD.dateTime)))
        graph.add((a.rule, RDF.type, a.Directive))
        graph.add((a.rule, q.traceabilityDueAt, Literal('2020-01-01T00:00:00Z', datatype=XSD.dateTime)))
        graph.add((a.work, RDF.type, a.WorkItem))
        graph.add((a.policy, RDF.type, a.Policy))
        def post(endpoint, body):
            self.assertTrue(body.get('verbose'))
            rows = []
            selected = graphs.get(body.get('graph'))
            if body.get('graph') is None:
                selected = Graph()
                for source in graphs.values():
                    selected += source
            for row in selected.query(body['query']):
                cells = {}
                for key, value in row.asdict().items():
                    cells[str(key)] = ({'value': str(value), 'datatype': str(value.datatype)}
                                       if isinstance(value, Literal) and value.datatype else str(value))
                rows.append(cells)
            return {'rows': rows, 'truncated': False}
        now = dt.datetime(2026,11,14,tzinfo=dt.timezone.utc)
        for predicate, target in ((a.trackedBy,a.work),(a.governedBy,a.policy)):
            graph.add((a.rule, predicate, Literal(str(target))))
        result = de.judge(post, 'rule', now)
        self.assertEqual((result['verdict'], result['lapse']), (de.UNTRACED, de.LAPSED))
        for predicate, target in ((a.trackedBy,a.work),(a.governedBy,a.policy)):
            graph.remove((a.rule, predicate, Literal(str(target))))
            graph.add((a.rule, predicate, target))
        result = de.judge(post, 'rule', now)
        self.assertEqual((result['verdict'], result['lapse']), (de.TRACED, de.NOT_LAPSED))
