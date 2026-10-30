"""Offline envelope attribution; no producer enrollment or live store calls."""
import hashlib
import json
import unittest
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import seed_work_item_sync as sync
import test_seed_work_item_sync as fixtures
PlaneError = fixtures.PlaneError


class EnvelopeWitness(unittest.TestCase):
    setUp = fixtures.Tick.setUp
    tearDown = fixtures.Tick.tearDown
    post = fixtures.Tick.post
    tick = fixtures.Tick.tick

    def fail_control(self):
        original = self.post
        def post(endpoint, body):
            if body.get('query', '').startswith('ASK '):
                return {'result': False, 'private': 'RESPONSE-CANARY'}
            return original(endpoint, body)
        self.post = post

    def test_new_write_and_stored_readback_bind_exact_request(self):
        self.fail_control()
        result = self.tick()
        request = json.loads(self.j.next()[2])['request']
        # ID from the canonical fixture, not a graph-wide aggregate write count.
        from test_seed_work_items import ID
        self.assertEqual(result['attempted_item'], ID)
        witness = result['envelope_witness']
        self.assertEqual(witness['branch'], 'new-write')
        self.assertEqual(witness['own_write'], 'proved')
        self.assertEqual(witness['graph'], request['graph'])
        self.assertEqual(witness['body_sha256'], hashlib.sha256(
            json.dumps(request, sort_keys=True).encode()).hexdigest())
        again = self.tick()
        self.assertEqual(again['envelope_witness'], {
            **witness, 'branch': 'stored-readback', 'own_write': 'not-attempted'})
        self.assertEqual((again['writes'], self.writes), (0, 1))
        self.assertNotIn('RESPONSE-CANARY', json.dumps(again))

    def test_same_version_is_distinct_and_never_writes(self):
        self.assertEqual(self.tick()['verified'], 1)
        from test_seed_change_custody import page
        self.j.retain(page(11))
        self.fail_control()
        result = self.tick()
        self.assertEqual(result['envelope_witness']['branch'], 'same-version')
        self.assertEqual(result['envelope_witness']['own_write'], 'not-attempted')
        self.assertEqual((result['writes'], self.writes), (0, 1))
        self.assertIsNone(self.j.next()[2])

    def test_wire_bytes_graph_body_collisions_and_redaction(self):
        # Independent literal byte oracle, including default ASCII escaping/spaces.
        request = {'turtle': 'BODY-CANARY é', 'graph': 'urn:example:one',
                   'actor': 'TOKEN-CANARY'}
        witness = {}
        sync.envelope_witness(witness, request)
        raw = b'{"actor": "TOKEN-CANARY", "graph": "urn:example:one", "turtle": "BODY-CANARY \\u00e9"}'
        self.assertEqual(witness['body_sha256'], hashlib.sha256(raw).hexdigest())
        for other in ({**request, 'graph': 'urn:example:two'},
                      {**request, 'turtle': 'OTHER-BODY'}):
            next_witness = {}
            sync.envelope_witness(next_witness, other)
            self.assertNotEqual(next_witness['body_sha256'], witness['body_sha256'])
        serialized = json.dumps(witness)
        for canary in ('BODY-CANARY', 'TOKEN-CANARY', 'turtle', 'actor'):
            self.assertNotIn(canary, serialized)
        # Transport serialization is independently observed at urllib's boundary.
        import planes
        class Reply:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, *args): return b'{}'
        captured = []
        def urlopen(req, **kwargs):
            captured.append(req.data)
            return Reply()
        with patch.object(planes.urllib.request, 'urlopen', urlopen):
            planes._post('/query', request, client='fixture')
        self.assertEqual(captured, [raw])

    def test_early_failure_and_invalid_graph_keep_explicit_unknown(self):
        self.unavailable = True
        result = self.tick()
        witness = result['envelope_witness']
        self.assertEqual((witness['graph'], witness['body_sha256'], witness['branch']),
                         ('UNKNOWN', 'UNKNOWN', 'UNKNOWN'))
        self.assertNotIn('control', result)
        for graph in ('', 'urn:bad\nCANARY', 'https://example.test/ private', None):
            with self.subTest(graph=graph):
                diagnostic = {'graph': 'UNKNOWN'}
                sync.envelope_witness(diagnostic, {'graph': graph})
                self.assertEqual(diagnostic['graph'], 'UNKNOWN')

    def test_private_projection_values_never_enter_receipt(self):
        original = sync.projection.body
        def body(*args, **kwargs):
            return {**original(*args, **kwargs), 'turtle': 'PRIVATE-BODY-CANARY',
                    'actor': 'PRIVATE-TOKEN-CANARY'}
        self.fail_control()
        with patch.object(sync.projection, 'body', body):
            result = self.tick()
        encoded = json.dumps(result)
        self.assertNotIn('PRIVATE-BODY-CANARY', encoded)
        self.assertNotIn('PRIVATE-TOKEN-CANARY', encoded)
        self.assertNotEqual(result['envelope_witness']['body_sha256'], 'UNKNOWN')

    def test_bad_stored_envelope_never_borrows_previous_tick(self):
        self.drop = True
        self.tick()
        item = self.j.db.execute('SELECT id FROM pending').fetchone()[0]
        self.j.db.execute('UPDATE pending SET body=? WHERE id=?', ('{', item))
        self.j.db.commit()
        result = self.tick()
        witness = result['envelope_witness']
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertEqual(witness['branch'], 'stored-readback')
        self.assertEqual((witness['graph'], witness['body_sha256']), ('UNKNOWN', 'UNKNOWN'))
        self.assertEqual(result['writes'], 0)

    def test_lost_write_is_not_attributed_as_proved(self):
        self.drop = True
        result = self.tick()
        self.assertEqual(result['envelope_witness']['own_write'], 'attempted')
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertEqual(self.writes, 1)


class MultiItemWitness(unittest.TestCase):
    ITEMS = fixtures.SerialTicks.ITEMS
    setUp = fixtures.SerialTicks.setUp
    tearDown = fixtures.SerialTicks.tearDown
    post = fixtures.SerialTicks.post
    tick = fixtures.SerialTicks.tick
    item_of = fixtures.SerialTicks.item_of

    def test_prior_success_new_native_failure_resets_all_evidence(self):
        first, second = self.ITEMS[:2]
        original = self.post
        def post(endpoint, body):
            if 'BIND(' in body.get('query', '') and self.item_of(body['query']) == second:
                raise PlaneError('HTTP 403 TOKEN-CANARY')
            return original(endpoint, body)
        self.post = post
        result = self.tick()
        self.assertEqual((result['item'], result['attempted_item']), (first, second))
        self.assertEqual((result['verified'], result['writes'], result['requests']), (1, 1, 6))
        witness = result['envelope_witness']
        self.assertEqual((witness['graph'], witness['body_sha256'], witness['branch']),
                         ('UNKNOWN', 'UNKNOWN', 'UNKNOWN'))
        self.assertEqual(witness['own_write'], 'not-attempted')
        self.assertNotIn('control', result)
        self.assertNotIn('TOKEN-CANARY', json.dumps(result))

    def test_prior_success_failed_control_binds_second_not_aggregate_writes(self):
        first, second = self.ITEMS[:2]
        original = self.post
        def post(endpoint, body):
            if body.get('query', '').startswith('ASK ') and self.item_of(body['query']) == second:
                return {'result': False}
            return original(endpoint, body)
        self.post = post
        result = self.tick()
        request = json.loads(self.j.db.execute('SELECT body FROM pending WHERE id=?', (second,)).fetchone()[0])['request']
        self.assertEqual((result['item'], result['attempted_item']), (first, second))
        self.assertEqual(result['envelope_witness']['body_sha256'], hashlib.sha256(
            json.dumps(request, sort_keys=True).encode()).hexdigest())
        self.assertEqual((result['verified'], result['writes'], result['requests']), (1, 2, 8))
