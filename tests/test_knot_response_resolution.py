"""Offline lost-response controls: one write, original completion binding only."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_seed_work_items import GRAPH, ID, rows
from test_seed_change_custody import page
import seed_change_custody as custody
import seed_work_items as native
import seed_work_item_sync as sync


class PlaneError(RuntimeError):
    pass


class LostResponse(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.board = native.Board(GRAPH, 'demo')
        self.journal = custody.Journal(Path(self.tmp.name) / 'journal.db', self.board,
                                       server='https://peer.example.test', initial_tx=9)
        self.addCleanup(self.journal.db.close)
        self.journal.retain(page())
        self.calls = []
        self.saved = None
        self.write_error = PlaneError('/knot gave no answer (TimeoutError: private-value)')
        self.status_change = lambda value: value
        self.effect = True
        self.status_error = None

    def post(self, endpoint, body, **kwargs):
        self.calls.append((endpoint, copy.deepcopy(body), kwargs))
        if endpoint == '/knot':
            self.saved = self.journal.next()[2]
            envelope = json.loads(self.saved)
            self.assertEqual(body, envelope['request'])
            self.assertEqual(kwargs.get('write_id'), envelope.get('write_id'))
            raise self.write_error
        if endpoint == '/knot/status':
            envelope = json.loads(self.saved)
            self.assertEqual(body, {'write_id': envelope['write_id'],
                                    'request': envelope['request']})
            self.assertEqual(kwargs, {'response_limit': 4096})
            if self.status_error:
                raise self.status_error
            return self.status_change({'contract': 'quipu.knot-completion/v1',
                                       'write_id': body['write_id'], 'state': 'finished',
                                       'receipt': {'conforms': True, 'replaced': True,
                                                   'count': 16, 'tx_id': 42}})
        query = body['query']
        if 'BIND(' in query:
            return {'rows': rows(), 'truncated': False}
        if query.startswith('ASK '):
            return {'result': self.effect}
        envelope = json.loads(self.saved)
        return {'rows': [{'v': envelope['description']}] if self.effect else [],
                'truncated': False}

    def tick(self, **kwargs):
        return sync.tick(native.Reader(None, self.board), self.journal, self.post,
                         changes=None, plane='urn:private:knowledge', actor='fixture',
                         completion=kwargs.pop('completion', True), **kwargs)

    def assert_frozen(self, result):
        self.assertEqual(result['status'], 'UNKNOWN', result)
        self.assertEqual(self.journal.next(), (ID, 10, self.saved))
        self.assertEqual(sum(e == '/knot' for e, _, _ in self.calls), 1)
        self.assertNotIn('private-value', json.dumps(result))

    def test_finished_after_timeout_acknowledges_original_without_resend(self):
        result = self.tick()
        self.assertEqual((result['status'], result['writes'], result['verified']),
                         ('OK', 1, 1), result)
        self.assertIsNone(self.journal.next())
        self.assertEqual([e for e, _, _ in self.calls],
                         ['/query', '/knot', '/knot/status', '/query', '/query'])
        self.assertEqual(result['completion'], 'finished-after-response-loss')

    def test_unknown_or_nonfinished_status_keeps_identical_body(self):
        for state in ('unknown', 'not_found', 'queued', 'running', 'stopped', 'not_started'):
            with self.subTest(state=state):
                case = LostResponse()
                case.setUp()
                try:
                    case.status_change = lambda value: {**value, 'state': state}
                    case.assert_frozen(case.tick())
                    self.assertEqual(sum(e == '/knot/status' for e, _, _ in case.calls), 1)
                finally:
                    case.doCleanups()

    def test_invalid_completion_controls(self):
        controls = [lambda v: {**v, 'write_id': '0' * 64},
                    lambda v: {**v, 'contract': 'other'},
                    lambda v: {**v, 'truncated': True},
                    lambda v: {**v, 'truncated': None},
                    lambda v: {**v, 'receipt': {'conforms': False, 'replaced': True, 'tx_id': 42}},
                    lambda v: {**v, 'receipt': {'conforms': True, 'replaced': True, 'tx_id': True}},
                    lambda v: {**v, 'receipt': {'conforms': True, 'replaced': False, 'tx_id': 42}},
                    lambda v: {**v, 'receipt': None},
                    lambda v: {**v, 'padding': 'x' * 4096},
                    lambda v: []]
        for control in controls:
            # A separate fixture is needed for each first delivery.
            case = LostResponse()
            case.setUp()
            try:
                case.status_change = control
                case.assert_frozen(case.tick())
            finally:
                case.doCleanups()

    def test_original_request_hash_mismatch_refuses_without_ack(self):
        # Native status authenticates the exact original request. A mismatch is
        # a refusal, not a substitute completion receipt or authority to resend.
        self.status_error = PlaneError('/knot/status failed: HTTP 409 private-value')
        self.assert_frozen(self.tick())

    def test_status_unavailable_preserves_body(self):
        self.status_error = TimeoutError('private-value')
        self.assert_frozen(self.tick())

    def test_finished_still_requires_exact_readback(self):
        self.effect = False
        self.assert_frozen(self.tick())

    def test_finished_with_partial_readback_stays_frozen(self):
        original = self.post
        def partial(endpoint, body, **kwargs):
            response = original(endpoint, body, **kwargs)
            if body.get('query', '').startswith('SELECT ?v '):
                response['truncated'] = True
            return response
        self.post = partial
        self.assert_frozen(self.tick())

    def test_newer_candidate_survives_original_effect_ack(self):
        original = self.post
        def newer(endpoint, body, **kwargs):
            response = original(endpoint, body, **kwargs)
            if endpoint == '/knot/status':
                self.journal.retain(page(11))
            return response
        self.post = newer
        result = self.tick()
        self.assertEqual((result['status'], result['verified']), ('OK', 1))
        self.assertEqual(self.journal.next(), (ID, 11, None))
        self.assertEqual(sum(e == '/knot' for e, _, _ in self.calls), 1)

    def test_socket_timeout_uses_the_same_completion_path(self):
        self.write_error = TimeoutError('private-value')
        self.assertEqual(self.tick()['verified'], 1)
        self.assertIsNone(self.journal.next())

    def test_status_lookup_obeys_existing_total_request_budget(self):
        with mock.patch.object(sync, 'TICK_REQUESTS', sync.ITEM_REQUESTS):
            self.assert_frozen(self.tick())
        self.assertLessEqual(len(self.calls), sync.ITEM_REQUESTS)

    def test_noncompletion_and_auth_failures_do_not_poll(self):
        self.assert_frozen(self.tick(completion=False))
        self.assertFalse(any(e == '/knot/status' for e, _, _ in self.calls))

    def test_auth_refusal_does_not_poll(self):
        self.write_error = PlaneError('credential refused private-value')
        self.assert_frozen(self.tick())
        self.assertFalse(any(e == '/knot/status' for e, _, _ in self.calls))

    def test_timeout_classification_is_fixed_and_payload_free(self):
        self.assertEqual(sync.failure_reason(self.write_error), 'no answer; outcome unknown')


if __name__ == '__main__':
    unittest.main()
