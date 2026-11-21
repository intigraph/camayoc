"""Validate actual record() output offline against the released domain shapes."""
from unittest.mock import patch
import unittest

from rdflib import Graph, URIRef, XSD
from pyshacl import validate
from camayoc_cli import definitions, memory
from test_aegis_directive_deadlines import DirectiveDeadlines


class LocalWriter:
    def __init__(self):
        self.writes = []

    def knot(self, turtle, graph, actor, source):
        self.writes.append(turtle)


class DirectiveWriterDeadline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        DirectiveDeadlines.setUpClass()
        cls.shapes = DirectiveDeadlines.shapes

    def write(self, deadline=None):
        args = dict(rule='Use the reviewed release path.', issuedBy='urn:test:operator',
                    appliesTo='crew', topic='release-path')
        if deadline is not None:
            args['traceabilityDueAt'] = deadline
        client = LocalWriter()
        with patch.object(memory, 'entity_exists', return_value=True), \
             patch.object(memory, 'find_topic', return_value='urn:test:topic'), \
             patch.object(memory, 'current_versions', return_value=[]):
            result = memory.record('directive', args, client=client)
        return result, client.writes

    def test_actual_writer_emits_typed_deadline_and_shape_refuses_without_it(self):
        for deadline in ('2030-11-28T08:00:00Z', '2030-11-28T09:00:00+01:00'):
            result, writes = self.write(deadline)
            self.assertEqual(result.code, 0, result.lines)
            self.assertEqual(len(writes), 1)
            data = Graph().parse(data=writes[0], format='turtle')
            values = list(data.objects(None, URIRef('https://w3id.org/intigraph/quechua/ns#traceabilityDueAt')))
            self.assertEqual(len(values), 1)
            self.assertEqual(values[0].datatype, XSD.dateTime)
            conforms, _, report = validate(data, shacl_graph=self.shapes, advanced=True)
            self.assertTrue(conforms, report)
        result, writes = self.write()
        self.assertEqual(result.code, 0, result.lines)
        conforms, _, report = validate(Graph().parse(data=writes[0], format='turtle'),
                                      shacl_graph=self.shapes, advanced=True)
        self.assertFalse(conforms, report)

    def test_invalid_dates_refuse_before_any_write(self):
        for deadline in ('2030-11-28', '2030-11-28T08:00:00', '2030-02-30T08:00:00Z',
                         '2030-11-28T08:00:00+99:00', 'garbage'):
            result, writes = self.write(deadline)
            self.assertEqual(result.code, 2, deadline)
            self.assertFalse(writes, deadline)
            self.assertIn('--traceability-due-at', '\n'.join(result.lines))

    def test_deadline_is_one_optional_generated_field_without_silent_default(self):
        field = next(f for f in definitions.load()['entities']['directive']['fields']
                     if f['name'] == 'traceabilityDueAt')
        self.assertEqual(field['maxCount'], 1)
        self.assertEqual(field['datatype'], 'xsd:dateTime')
        self.assertFalse(field['required'])
