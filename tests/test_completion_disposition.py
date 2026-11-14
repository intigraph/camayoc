"""Offline completion diagnostics cannot authorize a retry or expose payloads."""
import json
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import seed_work_item_sync as sync


class CompletionDisposition(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE verified (id TEXT, version INTEGER)')
        self.journal = SimpleNamespace(db=self.db)
        self.envelope = {'write_id': 'a' * 64, 'request': {'secret': 'PRIVATE'}, 'version': 2}
        self.status = {'contract': 'quipu.knot-completion/v1', 'write_id': 'a' * 64,
                       'state': 'stopped', 'receipt': {'conforms': True, 'replaced': True, 'tx_id': 0}}

    def run_status(self, status, absent=True):
        diagnostics = {}
        calls = []
        def post(route, body):
            calls.append((route, body))
            if isinstance(status, Exception):
                raise status
            return status
        with patch.object(sync, 'prove_absent', return_value=absent) as proof:
            result = sync.completed_without_effect(post, self.journal, self.envelope,
                                                   'fixture', diagnostics=diagnostics)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0], ('/knot/status', {'write_id': 'a' * 64,
                                                 'request': {'secret': 'PRIVATE'}}))
        self.assertNotIn('PRIVATE', json.dumps(diagnostics))
        self.assertNotIn('a' * 64, json.dumps(diagnostics))
        return result, diagnostics['completion_disposition'], proof.call_count

    def test_invalid_status_shapes_and_untrusted_states_stay_frozen(self):
        variants = [None, [], {}, {**self.status, 'contract': 'PRIVATE'},
                    {**self.status, 'write_id': 'PRIVATE'},
                    {**self.status, 'truncated': True},
                    {**self.status, 'truncated': 'PRIVATE'},
                    {**self.status, 'state': ['PRIVATE']}]
        for state in ('absent', 'evicted', 'unknown', 'running', 'finished', 'PRIVATE'):
            variants.append({**self.status, 'state': state})
        for status in variants:
            with self.subTest(status=status):
                result, summary, calls = self.run_status(status)
                self.assertFalse(result)
                self.assertEqual(calls, 0)
                self.assertEqual(summary['absence'], 'not-attempted')
                self.assertNotIn('PRIVATE', json.dumps(summary))

    def test_stopped_and_not_started_still_require_independent_absence(self):
        for state in ('stopped', 'not_started'):
            for absent in (False, True):
                result, summary, calls = self.run_status({**self.status, 'state': state}, absent)
                self.assertEqual(result, absent)
                self.assertEqual(calls, 1)
                self.assertEqual(summary['absence'], 'proved' if absent else 'unproved')
                self.assertEqual(summary['state'], state)
                self.assertTrue(summary['receipt_conforms'])
                self.assertTrue(summary['receipt_replaced'])
                self.assertTrue(summary['receipt_tx_valid'])

    def test_transport_error_retains_diagnostic_and_original_exception(self):
        for exc in (OSError('PRIVATE'), RuntimeError('PRIVATE')):
            diagnostics = {}
            with self.assertRaises(type(exc)):
                sync.completed_without_effect(lambda *args: (_ for _ in ()).throw(exc),
                                              self.journal, self.envelope, 'fixture',
                                              diagnostics=diagnostics)
            self.assertEqual(diagnostics['completion_disposition']['response'], 'unavailable')
            self.assertNotIn('PRIVATE', json.dumps(diagnostics))

    def test_missing_legacy_write_id_has_no_lookup(self):
        self.envelope.pop('write_id')
        diagnostics = {}
        with patch.object(sync, 'prove_absent') as proof:
            self.assertFalse(sync.completed_without_effect(
                lambda *args: self.fail('legacy envelope must not call server'),
                self.journal, self.envelope, 'fixture', diagnostics=diagnostics))
        proof.assert_not_called()
        self.assertEqual(diagnostics['completion_disposition']['response'], 'not-attempted')

    def test_refusal_is_preserved_without_response_text(self):
        class PlaneError(RuntimeError):
            pass
        diagnostics = {}
        exc = PlaneError('HTTP 403 PRIVATE')
        with self.assertRaises(PlaneError) as caught:
            sync.completed_without_effect(lambda *args: (_ for _ in ()).throw(exc),
                                          self.journal, self.envelope, 'fixture',
                                          diagnostics=diagnostics)
        self.assertIs(caught.exception, exc)
        self.assertEqual(diagnostics['completion_disposition']['response'], 'refused')
        self.assertNotIn('PRIVATE', json.dumps(diagnostics))
