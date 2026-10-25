"""Bounded coverage must discriminate both outcomes from UNKNOWN, without writes."""
import contextlib
import io
import json
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import exact_work_item_coverage as coverage
import backfill_work_items as legacy
from rdflib import Dataset, URIRef, Literal
from rdflib.namespace import RDF

IDS = ('demo-canary', 'demo-witness')
BOARD = 'https://example.invalid/board'


class Store:
    def __init__(self, second=True):
        self.store = Dataset()
        self.calls = []
        for graph, item, subject, kind, identifier in (
            (None, IDS[0], legacy.BASE_NS + IDS[0], legacy.WORKITEM_TYPES[0], legacy.BASE_NS + 'identifier'),
            (BOARD, IDS[1], coverage.native.item_iri(IDS[1]), legacy.WORKITEM_TYPES[2], 'https://schema.org/identifier'),
        ):
            if item == IDS[1] and not second:
                continue
            target = self.store.default_context if graph is None else self.store.graph(URIRef(graph))
            target.add((URIRef(subject), RDF.type, URIRef(kind)))
            target.add((URIRef(subject), URIRef(identifier), Literal(item)))

    def post(self, route, body):
        assert route == '/query'
        self.calls.append(body)
        graph = self.store.graph(URIRef(body['graph'])) if body.get('graph') else self.store.default_context
        answer = graph.query(body['query'])
        rows = [{str(key): str(value) for key, value in row.asdict().items()} for row in answer]
        return {'rows': rows, 'truncated': False, 'count': len(rows)}


class Coverage(unittest.TestCase):
    def run_store(self, store):
        return coverage.receipt(store.post, IDS, BOARD, IDS[0])

    def test_actual_sparql_parity_with_legacy_both_outcomes(self):
        for second in (True, False):
            with self.subTest(second=second):
                store = Store(second)
                expected = legacy.covered(store.post, (BOARD,))
                store.calls.clear()
                result = self.run_store(store)
                self.assertEqual(result['status'], 'COVERED' if second else 'NOT_COVERED')
                self.assertEqual(set(result['covered']), expected)
                self.assertEqual(result['requests'], 15)
                self.assertEqual(result['writes'], 0)
                self.assertEqual(len(store.calls), 15)
                for request in store.calls:
                    self.assertIn('VALUES ?w', request['query'])
                    self.assertTrue(request['query'].endswith('LIMIT 5'))
                    self.assertNotIn('OFFSET', request['query'])
                self.assertEqual(result['missing'], [] if second else [IDS[1]])

    def test_unrelated_data_never_enters_scope(self):
        store = Store(False)
        target = store.store.graph(URIRef(BOARD))
        target.add((URIRef('https://example.invalid/other'), RDF.type, URIRef(legacy.WORKITEM_TYPES[2])))
        target.add((URIRef('https://example.invalid/other'), URIRef('https://schema.org/identifier'), Literal(IDS[1])))
        self.assertEqual(self.run_store(store)['status'], 'NOT_COVERED')

    def test_server_compacted_query_subjects_match_full_iris_both_outcomes(self):
        for second in (True, False):
            store = Store(second)
            full = self.run_store(store)
            seen = []
            def compacted(route, body):
                result = store.post(route, body)
                for row in result['rows']:
                    if row['w'].startswith(legacy.BASE_NS):
                        row['w'] = 'a:' + row['w'][len(legacy.BASE_NS):]
                        seen.append(row['w'])
                return result
            actual = coverage.receipt(compacted, IDS, BOARD, IDS[0])
            self.assertTrue(seen)  # Actual SPARQL results exercise the decoder.
            for key in ('status', 'covered', 'missing', 'requests', 'writes'):
                self.assertEqual(actual[key], full[key])

    def test_only_exact_canonical_subject_forms_decode(self):
        foreign = ['a:other', 'other:' + IDS[0], 'aegis:' + IDS[0],
                   'a:' + IDS[0] + '-suffix', 'a:../' + IDS[0],
                   'a:%64emo-canary', 'a:demo-canary\n', '_:' + IDS[0],
                   'https://foreign.invalid/' + IDS[0],
                   {'value': 'a:' + IDS[0]}, {'iri': 'a:' + IDS[0]}, None, 1]
        for subject in foreign:
            with self.subTest(subject=subject):
                store = Store()
                def substituted(route, body):
                    result = store.post(route, body)
                    for row in result['rows']:
                        if row['w'] == legacy.BASE_NS + IDS[0]:
                            row['w'] = subject
                    return result
                result = coverage.receipt(substituted, IDS, BOARD, IDS[0])
                self.assertEqual(result['status'], 'UNKNOWN')
                self.assertNotIn('covered', result)
        for identifier in ('a:' + IDS[0], legacy.BASE_NS + IDS[0],
                           {'value': IDS[0]}, IDS[1]):
            with self.subTest(identifier=identifier):
                def conflict(route, body):
                    if '?id' in body['query']:
                        return {'rows': [{'w': 'a:' + IDS[0], 'id': identifier}],
                                'truncated': False}
                    return Store().post(route, body)
                result = coverage.receipt(conflict, IDS, BOARD, IDS[0])
                self.assertEqual(result['status'], 'UNKNOWN')

    def test_retained_triple_response_is_not_a_coverage_query_fixture(self):
        # A retained ?w/?p/?v response proves the wire spelling, but cannot be
        # replayed as a response to SELECT ?w or SELECT ?w ?id.
        retained = {'rows': [{'w': 'a:' + IDS[0], 'p': 'rdf:type',
                             'v': 'a:WorkItem'}], 'truncated': False}
        result = coverage.receipt(lambda *_: retained, IDS, BOARD, IDS[0])
        self.assertEqual(result['status'], 'UNKNOWN')
        # Minimal projection isolates subject decoding; actual query/result
        # semantics are covered by the RDF-backed parity test above.
        for truncated in (True, False):
            result = coverage.receipt(lambda *_: {'rows': [{'w': 'a:' + IDS[0]}],
                'truncated': truncated, 'error': 'refused'}, IDS, BOARD, IDS[0])
            self.assertEqual(result['status'], 'UNKNOWN')
        self.assertEqual(retained['rows'][0]['w'], 'a:' + IDS[0])

    def test_identifier_owner_conflict_is_unknown(self):
        store = Store()
        def conflict(route, body):
            if '?id' in body['query']:
                return {'rows': [{'w': legacy.BASE_NS + IDS[0], 'id': IDS[1]}], 'truncated': False}
            return store.post(route, body)
        result = coverage.receipt(conflict, IDS, BOARD, IDS[0])
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertNotIn('covered', result)

    def test_missing_control_is_unknown_not_zero(self):
        result = coverage.receipt(lambda *_: {'rows': [], 'truncated': False}, IDS, BOARD, IDS[0])
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertNotIn('covered', result)
        self.assertNotIn('missing', result)

    def test_refusal_malformed_truncated_foreign_and_conflicting_are_unknown(self):
        cases = [None, {}, {'error': 'deadline'}, {'rows': [], 'truncated': True},
                 {'rows': []}, {'rows': 'bad', 'truncated': False},
                 {'rows': [{'w': 'https://example.invalid/foreign'}], 'truncated': False},
                 {'rows': [{'w': legacy.BASE_NS + IDS[0]}] * 5, 'truncated': False},
                 {'rows': [{'w': legacy.BASE_NS + IDS[0], 'id': IDS[1]}], 'truncated': False}]
        for answer in cases:
            with self.subTest(answer=answer):
                result = coverage.receipt(lambda *_: answer, IDS, BOARD, IDS[0])
                self.assertEqual(result['status'], 'UNKNOWN')
                self.assertNotIn('covered', result)
                self.assertLessEqual(result['requests'], 15)

    def test_transport_failure_is_unknown_and_never_retried(self):
        calls = []
        def failed(*args):
            calls.append(args)
            raise TimeoutError('secret must not be echoed')
        result = coverage.receipt(failed, IDS, BOARD, IDS[0])
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertEqual(len(calls), 1)
        self.assertNotIn('secret', json.dumps(result))

    def test_slow_read_is_unknown(self):
        now = [0.0]
        def slow(*args):
            now[0] += 6.01
            return {'rows': [], 'truncated': False}
        result = coverage.receipt(slow, IDS, BOARD, IDS[0], clock=lambda: now[0])
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertEqual(result['requests'], 1)

    def test_bad_scope_has_zero_requests(self):
        for items, board, control in ((IDS[:1], BOARD, IDS[0]),
                ((IDS[0], IDS[0]), BOARD, IDS[0]), (IDS, BOARD, 'absent'),
                ((IDS[0], 'demo-> } INSERT'), BOARD, IDS[0]),
                (IDS, coverage.planes.plane_for('inferred'), IDS[0]),
                (IDS, 'https://example.invalid/> bad', IDS[0])):
            result = coverage.receipt(lambda *_: self.fail('request made'), items, board, control)
            self.assertEqual(result['status'], 'UNKNOWN')
            self.assertEqual(result['requests'], 0)

    def test_default_off_does_not_read_credentials_or_network(self):
        with patch.object(coverage.quipu_auth, 'token', side_effect=AssertionError('credential read')):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(coverage.main([]), 0)
            self.assertEqual(json.loads(output.getvalue()), {'status': 'OFF', 'requests': 0, 'writes': 0})

    def test_transport_budget_redirect_and_read_only(self):
        transport = coverage.Transport('http://127.0.0.1:1', 'synthetic')
        with self.assertRaises(coverage.Unknown):
            transport('/knot', {})
        with self.assertRaises(coverage.Unknown):
            coverage.NoRedirect().redirect_request(None, None, 302, '', {}, 'http://other.invalid')
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, size):
                self.size = size
                return b'x' * size
        response = Response()
        with patch.object(transport.opener, 'open', return_value=response) as opened:
            with self.assertRaises(coverage.Unknown):
                transport('/query', {'query': 'synthetic'})
            self.assertEqual(response.size, coverage.MAX_BYTES + 1)
            self.assertLessEqual(opened.call_args.kwargs['timeout'], 6)

    def test_whole_request_timer_interrupts_a_stalled_reader_and_is_restored(self):
        transport = coverage.Transport('http://127.0.0.1:1', 'synthetic')
        transport.end = time.monotonic() + 0.03
        original = coverage.signal.getsignal(coverage.signal.SIGALRM)
        with patch.object(transport.opener, 'open', side_effect=lambda *a, **k: time.sleep(1)):
            at = time.monotonic()
            with self.assertRaises(coverage.Unknown):
                transport('/query', {})
            self.assertLess(time.monotonic() - at, 0.5)
        self.assertEqual(coverage.signal.getitimer(coverage.signal.ITIMER_REAL), (0.0, 0.0))
        self.assertEqual(coverage.signal.getsignal(coverage.signal.SIGALRM), original)

    def test_cli_exit_codes_and_run_identity_are_not_schedule_attestation(self):
        for second, status, code in ((True, 'COVERED', 0), (False, 'NOT_COVERED', 1)):
            with self.subTest(status=status), patch.object(coverage, 'Transport', return_value=Store(second).post), \
                    patch.object(coverage.quipu_auth, 'token', return_value='synthetic'):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    actual = coverage.main(['--enable', '--item', IDS[0], '--item', IDS[1],
                        '--control', IDS[0], '--board-graph', BOARD, '--run-id', 'private-test'])
                result = json.loads(output.getvalue())
                self.assertEqual((actual, result['status']), (code, status))
                self.assertEqual(result['invocation'], 'caller-declared')
                self.assertEqual(len(result['source_sha256']), 5)
        with patch.object(coverage, 'Transport', side_effect=AssertionError('must not construct')):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(coverage.main(['--enable']), 2)
            self.assertEqual(json.loads(output.getvalue())['status'], 'UNKNOWN')


if __name__ == '__main__':
    unittest.main()
