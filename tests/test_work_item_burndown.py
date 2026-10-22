"""Private transaction/coverage/custody falsifiers for event burn-down."""
import copy
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import work_item_burndown as b
import seed_work_items as n

T = '2026-10-22T03:00:00Z'


def scope(owner='worker', priority=1):
    return b.Scope(n.Board('https://tracker.example/board', 'test'),
                   'https://graph.example', owner, priority, '2026-10')


def row(identifier, status='open', owner='worker', priority=1, version=1):
    result = {n.RDF+'type': {'ref': n.SCHEMA+'Action'},
              n.SCHEMA+'identifier': identifier, n.SEEDS+'status': status,
              n.SEEDS+'priority': priority, n.SCHEMA+'version': version,
              n.SCHEMA+'dateCreated': T, n.SCHEMA+'dateModified': T}
    if owner is not None:
        result[n.SCHEMA+'agent'] = {'ref': n.PRINCIPAL+owner}
    return result


def page(*events, since=10, watermark=None):
    records = []
    for tx, identifier, value in events:
        for sequence in range(2):
            records.append(dict(tx=tx, sequence=sequence, graph=scope().board.graph,
                                entity=n.ITEM+identifier, row=copy.deepcopy(value), op='assert',
                                timestamp=T))
    end = events[-1][0] if events else since
    return dict(records=records, next_tx=end, watermark_tx=watermark or end,
                capture='new_row', truncated=False)


class BurnDown(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'events.sqlite3'
        self.scope = scope()
        self.j = b.Journal(self.path, self.scope)
        self.addCleanup(self.j.db.close)
        self.source = {identifier: row(identifier, status=status)
                       for identifier, status in [('test-a', 'open'), ('test-b', 'in_progress')]}
        records = [b.state(self.scope, n.ITEM+k, v) for k, v in self.source.items()]
        self.opening = self.j.enroll(10, records, T)
        self.opening_values = json.loads(self.j.pending()[1])
        self.j.acknowledge(self.opening_values)

    def commit(self, value):
        self.j.consume(value, observed_at=T)
        found = json.loads(self.j.pending()[1])
        self.j.acknowledge(found)
        return [json.loads(x) for x in found.values()]

    def test_real_close_reopen_assignment_create_delete_match_independent_state_oracle(self):
        first = page((11, 'test-a', row('test-a', 'closed', version=2)),
                     (12, 'test-a', row('test-a', version=3)),
                     (13, 'test-b', row('test-b', 'in_progress', priority=2, version=2)),
                     (14, 'test-c', row('test-c')))
        values = self.commit(first)
        values += self.commit(page((15, 'test-c', {}), since=14))
        # Independent expected final cohort: A is reopened; B changed priority;
        # C was deleted. A creation and a close occurred, neither is invented by
        # enrollment, deletion, or re-opening.
        balances = {'open': 1, 'in_progress': 1, 'blocked': 0, 'deferred': 0}
        for v in values:
            if v['metric'] == 'delta':
                balances[v['status']] += v['amount']
        self.assertEqual(balances, {'open': 1, 'in_progress': 0, 'blocked': 0, 'deferred': 0})
        self.assertEqual(sum(v['amount'] for v in values if v['metric'] == 'created'), 1)
        self.assertEqual(sum(v['amount'] for v in values if v['metric'] == 'closed'), 1)
        self.assertEqual(self.j.cursor, 15)

    def test_assignment_in_and_out_are_balances_not_creations(self):
        values = self.commit(page((11, 'test-c', row('test-c', version=8)),
                                  (12, 'test-c', row('test-c', owner='other', version=9))))
        self.assertEqual([v['amount'] for v in values if v['metric'] == 'delta'], [1, -1])
        self.assertFalse(any(v['metric'] == 'created' for v in values))

    def test_repeated_full_rows_only_one_transition(self):
        values = self.commit(page((11, 'test-a', row('test-a', 'closed', version=2))))
        self.assertEqual(sum(v['metric'] == 'closed' for v in values), 1)

    def test_same_status_metadata_version_does_not_invent_flow(self):
        values = self.commit(page((11, 'test-a', row('test-a', version=2))))
        self.assertEqual([v['metric'] for v in values], ['checkpoint'])

    def test_empty_page_marks_ready_despite_unrelated_global_transactions(self):
        values = self.commit(page(since=10, watermark=100))
        self.assertEqual(values[0]['status'], 'ready')
        self.assertEqual(self.j.cursor, 10)

    def test_nonempty_page_remains_catching_up(self):
        values = self.commit(page((11, 'test-a', row('test-a', version=2))))
        self.assertEqual(values[-1]['status'], 'catching_up')

    def test_uncertain_effect_survives_restart_and_never_resends(self):
        self.j.consume(page((11, 'test-a', row('test-a', 'closed', version=2))), observed_at=T)
        retained = self.j.pending()
        with self.assertRaises(b.Unknown):
            self.j.consume(page((12, 'test-b', row('test-b', version=2))), observed_at=T)
        self.assertEqual(self.j.pending(), retained)
        with self.assertRaises(b.Unknown):
            self.j.acknowledge({})
        self.assertEqual(self.j.cursor, 10)
        reopened = b.Journal(self.path, self.scope)
        self.addCleanup(reopened.db.close)
        self.assertEqual(reopened.pending(), retained)
        reopened.acknowledge(json.loads(retained[1]))
        self.assertEqual(reopened.cursor, 11)

    def test_partial_conflicting_foreign_and_reordered_pages_do_not_advance(self):
        good = page((11, 'test-a', row('test-a', 'closed', version=2)))
        variants = []
        for key, value in [('capture', 'new_values'), ('truncated', True), ('next_tx', 12), ('watermark_tx', 9)]:
            bad = copy.deepcopy(good); bad[key] = value; variants.append(bad)
        bad = copy.deepcopy(good); bad['records'][1]['row'][n.SEEDS+'status'] = 'open'; variants.append(bad)
        bad = copy.deepcopy(good); bad['records'][0]['graph'] = 'https://foreign.example/board'; variants.append(bad)
        bad = copy.deepcopy(good); bad['records'].reverse(); variants.append(bad)
        bad = copy.deepcopy(good); del bad['records'][0]['row']; variants.append(bad)
        for bad in variants:
            with self.subTest(bad=bad):
                with self.assertRaises((b.Unknown, n.NativeUnknown)):
                    self.j.consume(bad, observed_at=T)
                self.assertIsNone(self.j.pending())
                self.assertEqual(self.j.cursor, 10)

    def test_regression_after_leaving_scope_still_refuses(self):
        self.commit(page((11, 'test-a', row('test-a', owner='other', version=3))))
        with self.assertRaises(b.Unknown):
            self.j.consume(page((12, 'test-a', row('test-a', version=2)), since=11), observed_at=T)

    def test_empty_or_different_cohort_cannot_enroll(self):
        for records in [[], [b.state(self.scope, n.ITEM+'test-x', row('test-x', owner='other'))]]:
            other = b.Journal(Path(self.tmp.name)/str(len(list(Path(self.tmp.name).iterdir()))), self.scope)
            self.addCleanup(other.db.close)
            with self.assertRaises(b.Unknown):
                other.enroll(10, records, T)

    def test_custody_identity_is_bound_to_peer_owner_priority_and_stream(self):
        before = self.path.read_bytes()
        with self.assertRaises(b.Unknown):
            b.Journal(self.path, scope(owner='other'))
        self.assertEqual(self.path.read_bytes(), before)
        foreign = Path(self.tmp.name)/'foreign.db'
        db = sqlite3.connect(foreign); db.execute('CREATE TABLE untouched(x)'); db.close()
        before = foreign.read_bytes()
        with self.assertRaises(b.Unknown):
            b.Journal(foreign, self.scope)
        self.assertEqual(foreign.read_bytes(), before)

    def test_enrollment_does_not_claim_created_closed_or_historical_daily_counts(self):
        values = [json.loads(v) for v in self.opening_values.values()]
        self.assertEqual({v['metric'] for v in values}, {'opening', 'checkpoint'})
        self.assertEqual(sum(v['amount'] for v in values if v['metric'] == 'opening'), 2)
        with self.assertRaises(b.Unknown):
            self.j.enroll(10, [], T)




class AggregateControls(unittest.TestCase):
    def body(self):
        import work_item_burndown_metrics as m
        self.m = m
        self.definition = json.loads(m.DEFINITION.read_text())
        self.params = m.parameters(scope(), '2026-10-22T03:01:00Z')
        rows = [dict(metric='opening', status=s, day=b.time(T), value=int(s == 'open')) for s in sorted(b.ACTIVE)]
        rows += [dict(metric='checkpoint', status='ready', day=b.time(T), value=15)]
        return dict(query=self.definition['name'], sparql=m.rendered(self.definition, self.params), rows=rows, count=len(rows), truncated=False)

    def test_no_history_invented_before_pinned_enrollment(self):
        body = self.body()
        samples = self.m.validate(body, self.definition, self.params)
        self.assertTrue(all(labels.get('day', '2026-10-22') == '2026-10-22' for _, labels, _ in samples))
        self.assertEqual(sum(value for name, _, value in samples if name == 'seeds_workitem_events_active'), 1)

    def test_catching_up_stale_missing_partial_and_negative_refuse(self):
        good = self.body()
        bads = []
        for field, value in [('truncated', True), ('query', 'other'), ('sparql', ''), ('count', 100)]:
            x = copy.deepcopy(good); x[field] = value; bads.append(x)
        for field, value in [('status', 'catching_up'), ('day', '2026-10-21T00:00:00Z')]:
            x = copy.deepcopy(good); x['rows'][-1][field] = value; bads.append(x)
        x = copy.deepcopy(good); x['rows'][0]['value'] = -1; bads.append(x)
        x = copy.deepcopy(good); x['rows'].pop(0); x['count'] -= 1; bads.append(x)
        for bad in bads:
            with self.subTest(bad=bad):
                with self.assertRaises(b.Unknown):
                    self.m.validate(bad, self.definition, self.params)

    def test_one_ask_success_timeout_and_cost_fail_never_retry(self):
        good = self.body(); calls = []
        def post(path, body):
            calls.append(path)
            return good
        self.m.collect(post, scope(), '2026-10-22T03:01:00Z')
        self.assertEqual(calls, ['/ask'])
        calls.clear()
        def timeout(path, body):
            calls.append(path)
            raise TimeoutError('private controlled timeout')
        with self.assertRaises(TimeoutError):
            self.m.collect(timeout, scope(), '2026-10-22T03:01:00Z')
        self.assertEqual(calls, ['/ask'])
        calls.clear(); ticks = iter([0, 4])
        with self.assertRaises(b.Unknown):
            self.m.collect(post, scope(), '2026-10-22T03:01:00Z', clock=lambda: next(ticks))
        self.assertEqual(calls, ['/ask'])


class TransportControls(unittest.TestCase):
    def setUp(self):
        BurnDown.setUp(self)
        import work_item_burndown_io as io
        self.io = io
        self.calls = []
        self.feed = page((11, 'test-a', row('test-a', 'closed', version=2)))
        self.tamper = False
        self.fail_write = False

    def post(self, path, body=None, *, method='POST'):
        self.calls.append((method, path.split('?')[0]))
        if path.startswith('/changes?'):
            self.assertEqual(method, 'GET')
            self.assertIn('capture=new_row', path)
            return self.feed
        if path == '/knot':
            if self.fail_write:
                raise TimeoutError('controlled indeterminate write')
            return dict(conforms=True, tx_id=100)
        if path == '/query':
            expected = json.loads(self.j.pending()[1]); rows = []
            for subject, raw in expected.items():
                v = json.loads(raw)
                rows.append(dict(s=subject, value=raw, at=v['at'], kind=b.PROFILE+v['metric'],
                                 status=v['status'], amount=v['amount']+(1 if self.tamper else 0), about=v['about']))
            limit=int(body['query'].rsplit('LIMIT ',1)[1])
            rows=rows[:limit]
            return dict(rows=rows, count=len(rows), truncated=False)
        raise AssertionError('unexpected request')

    def bulk(self, count=70):
        # Same admitted source/cohort limits as ordinary ingress. One real source
        # transaction must not become an un-sendable durable pending effect.
        states=[b.state(self.scope,n.ITEM+f'test-bulk-{i}',row(f'test-bulk-{i}')) for i in range(count)]
        with self.j.db:
            self.j.db.execute('DELETE FROM items')
            for value in states:
                self.j.db.execute('INSERT INTO items VALUES(?,?)',(value['subject'],json.dumps(value)))
        self.feed=page(*[(11,f'test-bulk-{i}',row(f'test-bulk-{i}',status='closed',version=2)) for i in range(count)])
        for sequence,record in enumerate(self.feed['records']):record['sequence']=sequence

    def test_legitimate_70_item_close_is_141_observations_and_advances_once(self):
        self.bulk()
        seen=[]
        def post(path,body=None,**kwargs):
            if path=='/knot':seen.append(len(json.loads(self.j.pending()[1])))
            return self.post(path,body,**kwargs)
        result=self.io.tick(post,self.j,observed_at=T)
        self.assertEqual(seen,[141])
        self.assertEqual(result['cursor'],11)
        self.assertIsNone(self.j.pending())
        self.assertEqual(self.calls,[('GET','/changes'),('POST','/knot'),('POST','/query')])
        self.feed=page(since=11)
        self.calls.clear()
        self.io.tick(self.post,self.j,observed_at=T)
        self.assertEqual(self.j.cursor,11)
        self.assertIsNone(self.j.pending())

    def test_large_unknown_write_recovery_is_read_only_including_reopened_custody(self):
        self.bulk();self.fail_write=True
        with self.assertRaises(TimeoutError):self.io.tick(self.post,self.j,observed_at=T)
        self.assertEqual(self.calls,[('GET','/changes'),('POST','/knot')])
        self.assertEqual(len(json.loads(self.j.pending()[1])),141)
        self.assertEqual(self.j.cursor,10)
        reopened=b.Journal(self.path,self.scope)
        self.addCleanup(reopened.db.close)
        self.calls.clear()
        # The fixture readback supplies the committed immutable observations.
        result=self.io.tick(self.post,reopened,observed_at=T)
        self.assertEqual(result['status'],'reconciled')
        self.assertEqual(self.calls,[('POST','/query')])
        self.assertEqual(reopened.cursor,11)
        self.assertIsNone(reopened.pending())

    def test_large_existing_pending_tamper_preserves_custody_then_recovers_read_only(self):
        self.bulk();self.j.consume(self.feed,observed_at=T)
        pending=self.j.pending();self.tamper=True
        with self.assertRaises(b.Unknown):self.io.tick(self.post,self.j,observed_at=T)
        self.assertEqual(self.j.pending(),pending)
        self.assertEqual(self.j.cursor,10)
        self.assertEqual(self.calls,[('POST','/query')])
        self.tamper=False;self.calls.clear()
        self.io.tick(self.post,self.j,observed_at=T)
        self.assertEqual(self.calls,[('POST','/query')])
        self.assertEqual(self.j.cursor,11)
        self.assertIsNone(self.j.pending())

    def test_large_existing_pending_absence_is_unknown_never_a_resend(self):
        self.bulk();self.j.consume(self.feed,observed_at=T);pending=self.j.pending()
        calls=[]
        def missing(path,body=None,**kwargs):
            calls.append(path)
            self.assertEqual(path,'/query')
            return dict(rows=[],count=0,truncated=False)
        for _ in range(2):
            with self.assertRaises(b.Unknown):self.io.tick(missing,self.j,observed_at=T)
        self.assertEqual(calls,['/query','/query'])
        self.assertEqual(self.j.pending(),pending)
        self.assertEqual(self.j.cursor,10)

    def test_readback_unrepresentable_effect_refuses_before_persisting(self):
        # A corrupt/oversized verification plan must not poison durable pending
        # custody even when its claimed Turtle body is deceptively small.
        raw=json.dumps(dict(at=T,metric='delta',status='open',amount=1,
                            about='urn:'+('a'*b.MAX_BYTES)))
        with self.assertRaises(b.Unknown):self.j.prepare(11,{},[('urn:effect',raw,'')])
        self.assertIsNone(self.j.pending())
        self.assertEqual(self.j.cursor,10)

    def test_tick_budgets_one_feed_one_write_one_readback(self):
        result = self.io.tick(self.post, self.j, observed_at=T)
        self.assertEqual(result['cursor'], 11)
        self.assertEqual(self.calls, [('GET', '/changes'), ('POST', '/knot'), ('POST', '/query')])

    def test_timeout_recovery_reads_only_and_never_resends(self):
        self.fail_write = True
        with self.assertRaises(TimeoutError):
            self.io.tick(self.post, self.j, observed_at=T)
        self.assertEqual(self.j.cursor, 10)
        self.calls.clear()
        result = self.io.tick(self.post, self.j, observed_at=T)
        self.assertEqual(result['status'], 'reconciled')
        self.assertEqual(self.calls, [('POST', '/query')])

    def test_valid_observed_json_cannot_mask_tampered_numeric_triple(self):
        self.tamper = True
        with self.assertRaises(b.Unknown):
            self.io.tick(self.post, self.j, observed_at=T)
        self.assertEqual(self.j.cursor, 10)
        self.assertIsNotNone(self.j.pending())

    def test_missing_credential_refuses_before_transport(self):
        from unittest.mock import patch
        calls = []
        client = self.io.Transport('https://private.example', opener=lambda *a, **k: calls.append(1))
        with patch.object(self.io.planes, 'auth_token', return_value=None):
            with self.assertRaises(b.Unknown):
                client('/query', dict(query='SELECT * WHERE {}'))
        self.assertEqual(calls, [])

    def test_pinned_opening_query_is_owner_priority_bounded_and_rejects_ambiguity(self):
        query = self.io.opening_query(self.scope)
        self.assertIn('<'+n.PRINCIPAL+'worker>', query)
        self.assertIn('<'+n.SEEDS+'priority> 1', query)
        self.assertIn('LIMIT 1001', query)
        r = dict(s=n.ITEM+'test-a', identifier='test-a', status='open', priority=1,
                 owner=n.PRINCIPAL+'worker', created=T, modified=T, version=1)
        good = dict(rows=[r], count=1, truncated=False)
        self.assertEqual(len(self.io.opening_records(self.scope, good)), 1)
        r['owner'] = n.PRINCIPAL+'other'
        with self.assertRaises(b.Unknown):
            self.io.opening_records(self.scope, good)


if __name__ == '__main__':
    unittest.main()
