import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import usage_generation_codec as c
from publish_work_cost import snapshots


class CodecTests(unittest.TestCase):
    def manifest(self):
        return {'version': c.VERSION, 'producer': 'https://example.org/producer',
                'harness': 'codex', 'session': 'fixture', 'horizon': '2026-10-24T00:00:00Z',
                'source_sha256': 'a'*64, 'previous': None,
                'parts': [[0, 'b'*64, 2, 65536]], 'records': 2}

    def validate(self, m):
        return c.validate_manifest(c.canonical(m), producer='https://example.org/producer',
                                   harness='codex', session='fixture', at='2026-10-24T00:00:00Z')

    def test_positive_and_empty(self):
        self.assertEqual(self.validate(self.manifest())['records'], 2)
        m = self.manifest(); m.update(parts=[], records=0)
        self.assertEqual(self.validate(m)['records'], 0)

    def test_canonical_refusals(self):
        for v in [float('nan'), float('inf'), 1.2, 2**53, '\ud800', {1: 'nonstring'}]:
            with self.subTest(value=repr(v)), self.assertRaises((ValueError, UnicodeError)):
                c.canonical(v)
        self.assertEqual(c.canonical({'b': 1, 'a': 'é'}), '{"a":"é","b":1}')

    def test_strict_schema_counts_and_identity(self):
        mutations = [('records', -1), ('records', True), ('records', 3),
                     ('source_sha256', 'BAD'), ('previous', 'relative'),
                     ('horizon', '2026-02-30T00:00:00Z'), ('session', 'other'),
                     ('harness', 'claude'), ('producer', 'https://example.org/other'),
                     ('version', 'future')]
        for key, value in mutations:
            m = self.manifest(); m[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.validate(m)
        for parts in [[[0,'b'*64,41,1]], [[0,'b'*64,2,65537]],
                      [[1,'b'*64,2,1]], [[0,'b'*64,True,1]],
                      [[0,'b'*64,1,1],[0,'c'*64,1,1]]]:
            m = self.manifest(); m['parts'] = parts
            with self.subTest(parts=parts), self.assertRaises(ValueError):
                self.validate(m)
        m = self.manifest(); m['extra'] = 1
        with self.assertRaises(ValueError): self.validate(m)

    def test_noncanonical_and_duplicate_keys(self):
        for raw in [json.dumps(self.manifest()), '{"version":1,"version":2}']:
            with self.assertRaises(ValueError):
                c.validate_manifest(raw, producer='https://example.org/producer', harness='codex',
                                    session='fixture', at='2026-10-24T00:00:00Z')

    @staticmethod
    def record(reason='no proven focus'):
        return {'harness':'codex','session':'s','id':'r','tokens':12,
                'counts':{'input_uncached':10,'cache_read_input':0,'cache_write_input':0,'output':2},
                'model':'fixture','model_source':'declared','at':'2026-10-24T00:00:00Z',
                'attribution':reason,'bead':'unattributed','agent':'fixture'}

    def test_stable_request_identity_independent_of_attribution(self):
        a = self.record(); b = copy.deepcopy(a); b.update(attribution='attributed',bead='p-one')
        self.assertEqual(c.record_iri(c.record_row(a)), c.record_iri(c.record_row(b)))
        self.assertNotEqual(c.digest(c.record_row(a)), c.digest(c.record_row(b)))
        b['harness'] = 'claude'
        self.assertNotEqual(c.record_iri(c.record_row(a)), c.record_iri(c.record_row(b)))

    def test_paired_attribution_state_is_not_recoverable_from_current_rdf(self):
        """Pin the schema gap: distinct contracted row hashes, identical native input RDF."""
        a = self.record('no proven focus'); b = self.record('overlapping focus')
        with patch('publish_work_cost.planes.plane_for', return_value='https://example.org/observed'):
            ta = next(snapshots({'errors':[], 'records':[a]}, 'fixture'))[0]['turtle']
            tb = next(snapshots({'errors':[], 'records':[b]}, 'fixture'))[0]['turtle']
        self.assertEqual(ta, tb)
        self.assertNotEqual(c.digest([c.record_row(a)]), c.digest([c.record_row(b)]))

    def test_pinned_proposal_bytes(self):
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(__import__('hashlib').sha256((root/'shapes/usage-generations.shapes.ttl').read_bytes()).hexdigest(),
                         'fb04e6bd343022a8ccd0aa9d5006cbf2f126a8f4f160a1f201a5b63512573906')

    def test_prepare_rows_refuses_duplicate_or_mixed_identity(self):
        a=self.record();b=copy.deepcopy(a);b['id']='r2'
        self.assertEqual(len(c.prepare_rows([b,a])),2)
        for records in [[a,a],[a,b|{'harness':'claude'}],[a,b|{'session':'other'}],
                        [a,b|{'agent':'other'}],[a|{'model':12}]]:
            with self.subTest(records=records),self.assertRaises(ValueError):c.prepare_rows(records)
