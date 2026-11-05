"""Fault-injected remote commit/timeout outcomes with real durable custody."""
import json
import unittest
from unittest import mock

import test_seed_work_item_sync as fixture
from test_seed_change_custody import page
from test_seed_work_items import ID, rows
import seed_work_items as native
import seed_work_item_sync as sync


class Reconciliation(unittest.TestCase):
    setUp = fixture.Tick.setUp
    tearDown = fixture.Tick.tearDown
    tick = fixture.Tick.tick

    def post(self, endpoint, body):
        if endpoint == '/knot':
            self.writes += 1
            self.last_envelope = json.loads(self.j.next()[2])
            lost = getattr(self, 'timeout_once', False)
            self.timeout_once = False
            if not lost or getattr(self, 'commit_before_timeout', False):
                self.description = self.last_envelope['description']
                self.commits = getattr(self, 'commits', 0) + 1
            if lost:
                raise TimeoutError('fault-injected unconfirmed writer response')
            return {'replaced': True, 'tx_id': 99, 'conforms': True}
        query = body['query']
        if 'BIND(' in query:
            return {'rows': rows(**{native.SCHEMA+'version': {
                'value': str(getattr(self, 'native_version', 3)),
                'datatype': 'xsd:integer'}}), 'truncated': False}
        if query.startswith('ASK '):
            if '?p ?o' in query:
                self.absence_queries = getattr(self, 'absence_queries', []) + [query]
                return getattr(self, 'absence_answer', {
                    'result': self.description is not None, 'truncated': False})
            return getattr(self, 'control_answer', {
                'result': self.description is not None, 'truncated': False})
        return getattr(self, 'description_answer', {
            'rows': [{'v': self.description}] if self.description else [],
            'truncated': False})

    def fail_write(self, *, committed=False):
        self.timeout_once = True
        self.commit_before_timeout = committed
        receipt = self.tick()
        self.assertEqual((receipt['status'], receipt['writes']), ('UNKNOWN', 1))
        self.assertIsNotNone(self.j.next()[2])
        return self.j.next()[2]

    def test_commit_then_timeout_verifies_after_restart_without_duplicate(self):
        self.fail_write(committed=True)
        self.j.db.close()
        self.j = fixture.sc.Journal(
            self.path, self.reader.board, server='https://peer-one.example.test')
        receipt = self.tick()
        self.assertEqual((receipt['status'], receipt['writes'], receipt['verified']),
                         ('OK', 0, 1))
        self.assertEqual((self.writes, self.commits), (1, 1))
        self.assertIsNone(self.j.next())

    def test_no_commit_then_timeout_releases_only_after_two_absence_proofs(self):
        self.fail_write()
        receipt = self.tick()
        self.assertEqual((receipt['status'], receipt['writes'], receipt['verified']),
                         ('OK', 0, 0))
        self.assertEqual(self.j.next(), (ID, 10, None))
        self.assertEqual(len(self.absence_queries), 2)
        self.assertIn('GRAPH ?g', self.absence_queries[0])
        self.assertNotIn('GRAPH', self.absence_queries[1])
        receipt = self.tick()
        self.assertEqual((receipt['status'], receipt['writes'], receipt['verified']),
                         ('OK', 1, 1))
        self.assertEqual((self.writes, self.commits), (2, 1))
        self.assertIsNone(self.j.next())

    def test_newer_feed_candidate_survives_release_of_absent_effect(self):
        self.fail_write()
        self.j.retain(page(11))
        receipt = self.tick()
        self.assertEqual((receipt['status'], receipt['verified']), ('OK', 0))
        self.assertEqual(self.j.next(), (ID, 11, None))
        self.assertEqual(self.tick()['verified'], 1)
        self.assertEqual(self.j.cursor, 11)

    def test_ambiguous_or_partial_read_keeps_exact_body_and_never_resends(self):
        stored = self.fail_write()
        for answer in ({}, {'result': None}, {'result': False, 'truncated': True},
                       {'result': False, 'truncated': 0}, ['not-an-object']):
            with self.subTest(answer=answer):
                self.control_answer = answer
                receipt = self.tick()
                self.assertEqual(receipt['status'], 'UNKNOWN')
                self.assertEqual(self.j.next()[2], stored)
                self.assertEqual(self.writes, 1)
                self.assertFalse(getattr(self, 'absence_queries', []))

    def test_ambiguous_absence_stays_frozen(self):
        stored = self.fail_write()
        for answer in ({}, {'result': True}, {'result': False, 'truncated': True},
                       {'result': False, 'truncated': 0}, ['not-an-object']):
            with self.subTest(answer=answer):
                self.absence_answer = answer
                self.assertEqual(self.tick()['status'], 'UNKNOWN')
                self.assertEqual(self.j.next()[2], stored)
                self.assertEqual(self.writes, 1)

    def test_conflicting_or_partial_facts_stay_frozen(self):
        stored = self.fail_write(committed=True)
        for answer in ({'rows': [{'v': 'different effect'}], 'truncated': False},
                       {'rows': [], 'truncated': True}, {'rows': {}},
                       {'rows': [{'v': self.description}]},
                       {'rows': [{'v': self.description}], 'truncated': 0}):
            with self.subTest(answer=answer):
                self.description_answer = answer
                self.assertEqual(self.tick()['status'], 'UNKNOWN')
                self.assertEqual(self.j.next()[2], stored)
                self.assertEqual((self.writes, self.commits), (1, 1))

    def test_missing_type_with_other_facts_is_not_absence(self):
        stored = self.fail_write(committed=True)
        self.control_answer = {'result': False, 'truncated': False}
        self.assertEqual(self.tick()['status'], 'UNKNOWN')
        self.assertEqual(self.j.next()[2], stored)
        self.assertEqual((self.writes, self.commits), (1, 1))

    def test_update_timeout_reconciles_with_previously_verified_facts_retained(self):
        self.assertEqual(self.tick()['verified'], 1)
        previous = self.description
        self.j.retain(page(11))
        self.native_version = 4
        self.fail_write()
        # A failed v4 snapshot never deletes the previously committed v3.
        self.assertEqual(self.description, previous)
        with mock.patch.object(sync.time, 'time', return_value=1000):
            self.tick()
        with mock.patch.object(sync.time, 'time', return_value=4600):
            self.tick()
            receipt = self.tick()
        self.assertEqual((receipt['status'], receipt['verified']), ('OK', 1))
        self.assertIsNone(self.j.next())
        self.assertEqual((self.writes, self.commits), (3, 2))

    def test_first_absence_read_cannot_release_custody_immediately(self):
        stored = self.fail_write()
        with mock.patch.object(sync.time, 'time', return_value=1000):
            self.tick()
        self.assertEqual(self.j.next()[2], stored)
        self.assertEqual(self.writes, 1)

    def test_delayed_commit_between_reads_is_verified_without_duplicate(self):
        stored = self.fail_write()
        with mock.patch.object(sync.time, 'time', return_value=1000):
            self.tick()
        # The original server operation completes after the first read.
        self.description = json.loads(stored)['description']
        self.commits = 1
        with mock.patch.object(sync.time, 'time', return_value=4600):
            receipt = self.tick()
        self.assertEqual((receipt['status'], receipt['verified']), ('OK', 1))
        self.assertIsNone(self.j.next())
        self.assertEqual((self.writes, self.commits), (1, 1))

    def test_same_verified_version_cannot_be_automatically_repaired(self):
        self.assertEqual(self.tick()['verified'], 1)
        self.j.retain(page(11))
        self.j.prepare(ID, 11, self.last_envelope)
        stored = self.j.next()[2]
        self.description = None
        self.assertEqual(self.tick()['status'], 'UNKNOWN')
        self.assertEqual(self.j.next()[2], stored)
        self.assertEqual(self.writes, 1)


if __name__ == '__main__':
    unittest.main()
