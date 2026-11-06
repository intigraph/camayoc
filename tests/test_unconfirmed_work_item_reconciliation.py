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
    def tick(self):
        return sync.tick(self.reader, self.j, self.post,
                         changes=lambda **args: page() if args['since'] < 10 else
                         page(records=[], next_tx=args['since'], watermark_tx=args['since']),
                         plane='urn:private:knowledge', actor='fixture', completion=True)

    def post(self, endpoint, body, **kwargs):
        if endpoint == '/knot/status':
            stored = json.loads(self.j.next()[2])
            self.assertEqual(body, {'write_id': stored['write_id'], 'request': stored['request']})
            return getattr(self, 'completion_answer', {
                'contract': 'quipu.knot-completion/v1', 'write_id': body['write_id'],
                'state': getattr(self, 'completion_state', 'stopped')})
        if endpoint == '/knot':
            self.assertEqual(kwargs.get('write_id'), json.loads(self.j.next()[2])['write_id'])
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
        if 'BIND(' in query and 'SELECT ?version ?v' not in query:
            return {'rows': rows(**{native.SCHEMA+'version': {
                'value': str(getattr(self, 'native_version', 3)),
                'datatype': 'integer'}}), 'truncated': False}
        if query.startswith('ASK '):
            if '?p ?o' in query:
                self.absence_queries = getattr(self, 'absence_queries', []) + [query]
                return getattr(self, 'absence_answer', {
                    'result': self.description is not None, 'truncated': False})
            return getattr(self, 'control_answer', {
                'result': self.description is not None, 'truncated': False})
        if 'SELECT ?version ?v' in query:
            return {'rows': [{'version': str(json.loads(self.description)['source_version']),
                              'v': self.description, 'datatype': 'integer'}] if self.description else [],
                    'truncated': False}
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
        self.completion_state = 'running'
        with mock.patch.object(sync.time, 'time', return_value=1000):
            self.tick()
        self.completion_state = 'stopped'
        with mock.patch.object(sync.time, 'time', return_value=4600):
            self.tick()
            receipt = self.tick()
        self.assertEqual((receipt['status'], receipt['verified']), ('OK', 1))
        self.assertIsNone(self.j.next())
        self.assertEqual((self.writes, self.commits), (3, 2))

    def test_first_absence_read_cannot_release_custody_immediately(self):
        stored = self.fail_write()
        self.completion_state = 'running'
        with mock.patch.object(sync.time, 'time', return_value=1000):
            self.tick()
        self.assertEqual(self.j.next()[2], stored)
        self.assertEqual(self.writes, 1)

    def test_delayed_commit_between_reads_is_verified_without_duplicate(self):
        stored = self.fail_write()
        self.completion_state = 'running'
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

    def test_missing_or_untrusted_completion_never_releases(self):
        stored = self.fail_write()
        write_id = json.loads(stored)['write_id']
        for answer in ({}, {'state': 'stopped'},
                       {'contract': 'quipu.knot-completion/v0', 'write_id': write_id, 'state': 'stopped'},
                       {'contract': 'quipu.knot-completion/v1', 'write_id': '0'*64, 'state': 'stopped'},
                       *({'contract': 'quipu.knot-completion/v1', 'write_id': write_id, 'state': state}
                         for state in ('queued', 'running', 'unknown', 'finished')),
                       {'contract': 'quipu.knot-completion/v1', 'write_id': write_id,
                        'state': 'stopped', 'truncated': 0}):
            with self.subTest(answer=answer):
                self.completion_answer = answer
                self.assertEqual(self.tick()['status'], 'UNKNOWN')
                self.assertEqual(self.j.next()[2], stored)
                self.assertEqual(self.writes, 1)

    def test_legacy_pending_body_cannot_gain_retroactive_completion(self):
        stored = json.loads(self.fail_write())
        del stored['write_id']
        legacy = json.dumps(stored, sort_keys=True, separators=(',', ':'))
        self.j.db.execute('UPDATE pending SET body=?', (legacy,))
        for _ in range(2):
            self.assertEqual(self.tick()['status'], 'UNKNOWN')
            self.assertEqual(self.j.next()[2], legacy)
            self.assertEqual(self.writes, 1)

    def test_retry_allocates_new_identity_and_preserves_original(self):
        first = json.loads(self.fail_write())['write_id']
        self.assertEqual(self.tick()['status'], 'OK')
        self.assertEqual(self.tick()['verified'], 1)
        self.assertNotEqual(self.last_envelope['write_id'], first)
        self.assertEqual(len(self.last_envelope['write_id']), 64)

    def test_previous_version_absence_query_uses_real_rdf_constraints(self):
        from rdflib import Dataset, URIRef, RDF
        self.assertEqual(self.tick()['verified'], 1)
        prior = self.last_envelope
        self.j.retain(page(11))
        self.native_version = 4
        envelope = json.loads(self.fail_write())
        dataset = Dataset()
        graph = dataset.graph(URIRef(prior['request']['graph']))
        graph.parse(data=prior['request']['turtle'], format='turtle')
        def post(endpoint, body):
            if endpoint == '/knot/status':
                return {'contract': 'quipu.knot-completion/v1',
                        'write_id': body['write_id'], 'state': 'stopped'}
            result = dataset.query(body['query'])
            return {'rows': [{str(name): str(value) for name, value in row.asdict().items()}
                             for row in result], 'truncated': False}
        self.assertTrue(sync.completed_without_effect(post, self.j, envelope, ID))
        subject = URIRef(sync.projection.A + ID)
        triple = (subject, RDF.type, URIRef(sync.projection.A + 'WorkItem'))
        graph.remove(triple)
        self.assertFalse(sync.completed_without_effect(post, self.j, envelope, ID))
        graph.add(triple)
        graph.parse(data=envelope['request']['turtle'], format='turtle')
        self.assertFalse(sync.completed_without_effect(post, self.j, envelope, ID))

    def test_extra_version_fact_cannot_be_hidden_by_previous_version_match(self):
        from rdflib import Dataset, URIRef, Literal
        self.assertEqual(self.tick()['verified'], 1)
        prior = self.last_envelope
        self.j.retain(page(11))
        self.native_version = 4
        envelope = json.loads(self.fail_write())
        dataset = Dataset()
        graph = dataset.graph(URIRef(prior['request']['graph']))
        graph.parse(data=prior['request']['turtle'], format='turtle')
        def post(endpoint, body):
            if endpoint == '/knot/status':
                return {'contract': 'quipu.knot-completion/v1',
                        'write_id': body['write_id'], 'state': 'stopped'}
            result = dataset.query(body['query'])
            return {'rows': [{str(name): str(value) for name, value in row.asdict().items()}
                             for row in result], 'truncated': False}
        self.assertTrue(sync.completed_without_effect(post, self.j, envelope, ID))
        graph.add((URIRef(sync.projection.A + ID), URIRef(native.SCHEMA + 'version'),
                   Literal(5)))
        self.assertFalse(sync.completed_without_effect(post, self.j, envelope, ID))
        graph.set((URIRef(sync.projection.A + ID), URIRef(native.SCHEMA + 'version'),
                   Literal('3')))
        self.assertFalse(sync.completed_without_effect(post, self.j, envelope, ID))

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
