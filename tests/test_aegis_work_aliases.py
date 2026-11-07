"""Raw RDF gates; no endpoint rewriting, inference or production writes."""
from pathlib import Path
import unittest
from rdflib import Graph, Namespace, RDF, RDFS, URIRef

ROOT = Path(__file__).resolve().parents[1]
AEGIS = Namespace('http://aegis.gastown.local/ontology/')
SCHEMA = Namespace('https://schema.org/')
ICAL = Namespace('http://www.w3.org/2002/12/cal/ical#')
SKOS = Namespace('http://www.w3.org/2004/02/skos/core#')
DCTERMS = Namespace('http://purl.org/dc/terms/')


def answers(graph):
    return {name: {row[0] for row in graph.query(
        (ROOT/f'competency/aegis-work-aliases/{name}.rq').read_text())}
        for name in ('canonical', 'WorkItem', 'Bead', 'Vtodo')}


def populated():
    graph = Graph().parse(ROOT/'ontology/core.ttl')
    for suffix in ('one', 'two'):
        item = URIRef(f'https://example.org/work/{suffix}')
        calendar = URIRef(f'https://example.org/calendar/{suffix}')
        graph.add((item, RDF.type, SCHEMA.Action))
        graph.add((calendar, RDF.type, ICAL.Vtodo))
        graph.add((item, SKOS.exactMatch, calendar))
    return graph


class RawAliases(unittest.TestCase):
    def test_positive_returns_canonical_ids_not_calendar_ids(self):
        result = answers(populated())
        expected = {URIRef('https://example.org/work/'+s) for s in ('one', 'two')}
        self.assertEqual(list(result.values()), [expected]*4)

    def test_canonical_writes_need_no_duplicate_legacy_types(self):
        graph = populated()
        self.assertEqual(list(graph.subjects(RDF.type, AEGIS.WorkItem)), [])
        self.assertEqual(list(graph.subjects(RDF.type, AEGIS.Bead)), [])
        result = answers(graph)
        self.assertEqual(list(result.values()), [result['canonical']]*4)

    def test_missing_or_wrong_raw_class_mapping_is_visible(self):
        for mode in ('replacement', 'subclass', 'wrong-replacement'):
            graph = populated()
            if mode == 'subclass':
                graph.remove((AEGIS.Bead, RDFS.subClassOf, AEGIS.WorkItem))
            else:
                graph.remove((AEGIS.WorkItem, DCTERMS.isReplacedBy, SCHEMA.Action))
                if mode == 'wrong-replacement':
                    graph.add((AEGIS.WorkItem, DCTERMS.isReplacedBy, SCHEMA.Thing))
            result = answers(graph)
            self.assertEqual(len(result['canonical']), 2)
            self.assertEqual(result['Bead'], set(), mode)
            if mode != 'subclass':
                self.assertEqual(result['WorkItem'], set(), mode)

    def test_missing_wrong_or_identity_calendar_link_fails_population_gate(self):
        item = URIRef('https://example.org/work/one')
        calendar = URIRef('https://example.org/calendar/one')
        for mode in ('missing', 'wrong-type', 'identity'):
            graph = populated()
            graph.remove((item, SKOS.exactMatch, calendar))
            if mode == 'wrong-type':
                graph.add((item, SKOS.exactMatch, URIRef('https://example.org/not-calendar')))
            if mode == 'identity':
                graph.add((item, RDF.type, ICAL.Vtodo))
                graph.add((item, SKOS.exactMatch, item))
            result = answers(graph)
            self.assertNotEqual(result['Vtodo'], result['canonical'], mode)
            self.assertEqual(len(result['Vtodo']), 1)

    def test_existing_exact_match_relation_can_be_traversed_in_either_direction(self):
        graph = populated()
        for item, _, calendar in list(graph.triples((None, SKOS.exactMatch, None))):
            graph.remove((item, SKOS.exactMatch, calendar))
            graph.add((calendar, SKOS.exactMatch, item))
        result = answers(graph)
        self.assertEqual(result['Vtodo'], result['canonical'])

    def test_empty_graph_cannot_supply_a_positive_control(self):
        self.assertEqual(answers(Graph()), {name: set() for name in ('canonical','WorkItem','Bead','Vtodo')})


if __name__ == '__main__':
    unittest.main()
