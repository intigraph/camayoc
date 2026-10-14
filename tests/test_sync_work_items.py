"""Standing tracker delivery is bounded, verifiable and safe after lost replies."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import sync_work_items as sync

RECORD = {'id': 'proj-a', 'title': 'Current work', 'created_at': '2026-09-20T00:00:00Z',
          'status': 'in_progress', 'assignee': 'worker', 'priority': 1}


class Delivery(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'state.json'
        self.state = {}
        self.calls = []
        self.present = True
        self.control = True
        self.lost = False

    def post(self, endpoint, body):
        self.calls.append((endpoint, copy.deepcopy(body)))
        if endpoint == '/episode':
            # Write-ahead persistence must precede every write, including retry.
            saved = json.loads(self.path.read_text())
            self.assertEqual(body, saved['items'][body['nodes'][0]['name']]['pending']['body'])
            if self.lost:
                raise TimeoutError('lost response')
            return {'outcome': 'unchanged', 'count': 0}
        if 'LIMIT 1' in body['query']:
            return {'rows': [{'s': 'control'}] if self.control else []}
        return {'rows': [{'t': 'WorkItem', 'o': 'Observation'}] if self.present else []}

    def tick(self, records=None, now=1000, max_items=1):
        return sync.tick([RECORD] if records is None else records, self.state, self.path,
                         actor='tracker', source='br:authoritative', now=now, post=self.post,
                         max_items=max_items)

    def test_a_refused_write_records_its_http_status_and_never_the_body(self):
        import io
        import urllib.error
        import planes

        def refusing(endpoint, body):
            if endpoint == '/episode':
                try:
                    raise urllib.error.HTTPError('http://q/episode', 400, 'Bad', {},
                                                 io.BytesIO(b'secret-ish body'))
                except urllib.error.HTTPError as e:
                    raise planes.PlaneError('/episode failed: HTTP 400 body') from e
            return self.post(endpoint, body)

        self.present = False
        receipt = sync.tick([RECORD], self.state, self.path, actor='tracker',
                            source='br:authoritative', now=1000, post=refusing)
        entry = self.state['items']['proj-a']
        self.assertEqual((entry['error'], entry['error_status']), ('PlaneError', 400))
        self.assertEqual(receipt['error_status'], 400)
        self.assertNotIn('secret-ish', json.dumps(receipt) + self.path.read_text())

    def test_a_status_less_failure_records_no_status(self):
        self.assertIsNone(sync.http_status(TimeoutError('lost')))
        self.assertIsNone(sync.http_status(ValueError('absent')))

    def test_explicit_batch_caps_distinct_items_requests_and_backoff(self):
        records = [{**RECORD, 'id': f'proj-{i}'} for i in range(7)]
        result = self.tick(records, max_items=4)
        self.assertEqual((4, 4, 12, 3),
                         tuple(result[k] for k in ('verified', 'writes', 'requests', 'backlog')))
        self.assertEqual(4, len(set(result['items'])))
        before = len(self.calls)
        result = self.tick(records, now=1059.99, max_items=4)
        self.assertEqual(('BACKOFF', 0, []),
                         (result['mode'], result['requests'], result['items']))
        self.assertEqual(before, len(self.calls))

    def test_batch_keeps_failed_record_visible_while_delivering_others(self):
        records = [{**RECORD, 'id': f'proj-{i}'} for i in range(4)]
        real_post = self.post
        def post(endpoint, body):
            if endpoint == '/episode' and body['nodes'][0]['name'] == 'proj-0':
                self.calls.append((endpoint, copy.deepcopy(body)))
                raise TimeoutError('lost response')
            return real_post(endpoint, body)
        self.post = post
        result = self.tick(records, max_items=4)
        self.assertEqual(('DEGRADED', 3, 1, 4, 10),
                         tuple(result[k] for k in ('status', 'verified', 'indeterminate', 'writes', 'requests')))
        self.assertIn('pending', self.state['items']['proj-0'])

    def test_batch_cannot_turn_two_scheduled_absent_reads_into_same_tick_retry(self):
        self.lost = True
        self.tick()
        original = self.calls[0][1]
        self.lost = False
        self.present = False
        records = [{**RECORD, 'title': 'new version'}, *[
            {**RECORD, 'id': f'proj-{i}'} for i in range(3)]]
        for now in (1060, 1120):
            self.tick(records, now=now, max_items=4)
            writes = [b for p, b in self.calls if p == '/episode' and b['name'] == original['name']]
            self.assertEqual([original], writes)
        self.present = True
        self.tick(records, now=1180, max_items=4)
        writes = [b for p, b in self.calls if p == '/episode' and b['name'] == original['name']]
        self.assertEqual([original, original], writes)

    def test_expiring_policy_reverts_to_one_without_restarting(self):
        policy_path = self.path.with_suffix('.batch.json')
        self.assertEqual((1, None), sync.batch_policy(policy_path, 1000))
        policy = {'max_items': 4, 'starts_at': 1000, 'expires_at': 4600, 'review': 'review-receipt'}
        policy_path.write_text(json.dumps(policy))
        for now, expected in ((999, 1), (1000, 4), (4599.99, 4), (4600, 1)):
            self.assertEqual((expected, policy), sync.batch_policy(policy_path, now))
        records = [{**RECORD, 'id': f'proj-{i}'} for i in range(7)]
        self.assertEqual(4, self.tick(records, now=4540,
                                    max_items=sync.batch_policy(policy_path, 4540)[0])['verified'])
        self.assertEqual(1, self.tick(records, now=4600,
                                    max_items=sync.batch_policy(policy_path, 4600)[0])['verified'])

    def test_invalid_or_unbounded_policy_refuses(self):
        policy_path = self.path.with_suffix('.batch.json')
        good = {'max_items': 4, 'starts_at': 1000, 'expires_at': 4600, 'review': 'review-receipt'}
        for field, value in [('max_items', 5), ('max_items', True), ('max_items', 0),
                             ('expires_at', 4601), ('expires_at', float('nan')),
                             ('starts_at', True), ('starts_at', -1), ('review', '')]:
            with self.subTest(field=field, value=value):
                policy_path.write_text(json.dumps({**good, field: value}))
                with self.assertRaises(ValueError):
                    sync.batch_policy(policy_path, 1000)
        for limit in (0, 5, True):
            with self.assertRaises(ValueError):
                self.tick(max_items=limit)
        self.assertEqual([], self.calls)

    def test_one_hour_jitter_and_arrivals_drain_only_with_opt_in_batch(self):
        results = {}
        for limit in (1, 4):
            self.state = {}
            self.calls = []
            records = [{**RECORD, 'id': f'proj-{i:04}'} for i in range(194)]
            backoffs = verified = 0
            for minute in range(60):
                # Four new outstanding entries every five minutes, with the
                # measured two-thirds eligibility pattern from scheduler jitter.
                if minute % 5 != 4:
                    records.append({**RECORD, 'id': f'arrival-{minute:04}'})
                result = self.tick(records, now=1000 + minute * 60 + (minute % 3) / 10,
                                   max_items=limit)
                backoffs += result.get('mode') == 'BACKOFF'
                verified += result['verified']
                self.assertLessEqual(result['requests'], 3 * limit)
            results[limit] = result['backlog'], backoffs, verified
        self.assertGreater(results[1][0], 194)
        self.assertLess(results[4][0], 100)
        self.assertEqual(results[1][1], results[4][1])
        self.assertEqual(4 * results[1][2], results[4][2])

    def test_one_record_write_plus_two_reads_and_named_plane(self):
        result = self.tick([RECORD, {**RECORD, 'id': 'proj-b'}])
        self.assertEqual(3, result['requests'])
        self.assertEqual(1, result['verified'])
        self.assertEqual(1, result['backlog'])
        self.assertEqual(['/episode', '/query', '/query'], [p for p, _ in self.calls])
        self.assertTrue(all(b['graph'] == sync.planes.plane_for('observed') for _, b in self.calls))
        self.assertEqual('br:authoritative#proj-a', self.calls[0][1]['source'])
        self.assertIn('FILTER(?t =', self.calls[-1][1]['query'])
        self.assertIn('observedValue', self.calls[-1][1]['query'])

    def test_unchanged_success_needs_readback_not_count(self):
        self.assertEqual('OK', self.tick()['status'])
        self.assertNotIn('pending', self.state['items']['proj-a'])

    def test_recovery_clock_survives_retry_restart_and_tracker_changes(self):
        self.lost = True
        result = self.tick()
        self.assertEqual((0, 0), (result['pending_oldest_seconds'], result['exhausted_retries']))
        body = copy.deepcopy(self.state['items']['proj-a']['pending']['body'])
        self.state = json.loads(self.path.read_text())
        self.lost = False
        self.present = False
        changed = [{**RECORD, 'title': 'Changed'}]
        for now in (1060, 1120, 1180):
            result = self.tick(changed, now=now)
            self.assertEqual(now - 1000, result['pending_oldest_seconds'])
            self.assertEqual(1000, self.state['items']['proj-a']['pending']['started_at'])
        self.assertEqual(2, self.state['items']['proj-a']['pending']['attempts'])
        self.assertEqual([body, body], [b for p, b in self.calls if p == '/episode'])
        self.present = True
        result = self.tick(changed, now=1240)
        self.assertEqual((0, 0, 0), (result['indeterminate'], result['pending_oldest_seconds'],
                                    result['exhausted_retries']))

    def test_backoff_recomputes_age_and_exhaustion_without_requests(self):
        self.lost = True
        self.tick()
        self.state['next_tick'] = 10000
        self.state['items']['proj-a']['pending']['attempts'] = sync.MAX_ATTEMPTS
        self.state['receipt']['indeterminate'] = 0  # stale receipt after write-ahead save
        before = len(self.calls)
        result = self.tick(now=5000)
        self.assertEqual(('BACKOFF', 4000, 1, 1),
                         (result['mode'], result['pending_oldest_seconds'],
                          result['exhausted_retries'], result['indeterminate']))
        self.assertEqual((0, 0, before), (result['writes'], result['requests'], len(self.calls)))

    def test_final_failed_attempt_exposes_exhaustion_until_confirmed_without_fourth_write(self):
        self.lost = True
        self.present = False
        for now in (1000, 1060, 1120, 1180, 1240, 1300, 1360):
            result = self.tick(now=now)
        self.assertEqual((1, 360, 1), (result['indeterminate'],
                                      result['pending_oldest_seconds'], result['exhausted_retries']))
        self.assertEqual(3, len([p for p, _ in self.calls if p == '/episode']))
        self.lost = False
        self.present = True
        result = self.tick(now=2260)
        self.assertEqual((0, 0, 0), (result['indeterminate'],
                                    result['pending_oldest_seconds'], result['exhausted_retries']))
        self.assertEqual(3, len([p for p, _ in self.calls if p == '/episode']))

    def test_backlog_wait_does_not_age_a_new_recovery(self):
        self.lost = True
        self.state['items'] = {'proj-a': {'first_seen': 0, 'last_attempt': 0, 'due_since': 0}}
        result = self.tick(now=10000)
        self.assertEqual(10000, result['oldest_seconds'])
        self.assertEqual(0, result['pending_oldest_seconds'])

    def test_unknown_legacy_and_invalid_recovery_clocks_never_become_zero(self):
        for start in (None, True, '1000', -1, float('nan'), float('inf'), 2000, 10 ** 1000):
            with self.subTest(start=start):
                pending = {'attempts': 1}
                if start is not None:
                    pending['started_at'] = start
                result = sync.recovery_metrics({'a': {'pending': pending}}, 1000)
                self.assertEqual((1, -1, 0), (result['indeterminate'],
                                             result['pending_oldest_seconds'],
                                             result['exhausted_retries']))

    def test_oldest_pending_and_exhaustion_include_every_retained_body(self):
        entries = {'a': {'pending': {'started_at': 1000, 'attempts': 1}},
                   'b': {'pending': {'started_at': 2000, 'attempts': 3}}}
        result = sync.recovery_metrics(entries, 5000)
        self.assertEqual((2, 4000, 1), (result['indeterminate'],
                                       result['pending_oldest_seconds'], result['exhausted_retries']))
        entries['b']['pending']['attempts'] = '3'
        self.assertEqual(-1, sync.recovery_metrics(entries, 5000)['exhausted_retries'])

    def test_cli_publishes_known_zero_and_unknown_recovery_metrics(self):
        import io
        from contextlib import redirect_stdout
        argv = ['sync', '--db', str(self.path.parent / 'tracker.db'),
                '--state', str(self.path), '--actor', 'tracker',
                '--source', 'br:authoritative', '--publish-status']
        for failure in (False, True):
            with self.subTest(failure=failure), patch.object(sys, 'argv', argv), \
                    patch.object(sync, 'collect', **({'side_effect': ValueError('fixture')}
                                                     if failure else {'return_value': []})), \
                    patch.object(sync.camayoc_metrics, 'push', return_value=(True, '')) as push, \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(2 if failure else 0, sync.main())
                exposition = push.call_args.args[1]
                for metric in ('pending_oldest_seconds', 'exhausted_retries'):
                    self.assertIn(f'camayoc_workitem_ingress_{metric} {-1 if failure else 0}',
                                  exposition)

    def test_unchanged_response_with_missing_data_is_not_success(self):
        self.present = False
        self.assertEqual('UNKNOWN', self.tick()['status'])
        self.assertIn('pending', self.state['items']['proj-a'])
        self.assertNotIn('version', self.state['items']['proj-a'])

    def test_failed_control_never_authorizes_retry(self):
        self.control = False
        self.tick()
        for now in (1060, 1120, 1180):
            self.tick(now=now)
        self.assertEqual(1, len([p for p, _ in self.calls if p == '/episode']))
        self.assertEqual(0, self.state['items']['proj-a']['pending']['absent_reads'])

    def test_timeout_landed_reconciles_without_repost(self):
        self.lost = True
        self.assertEqual('UNKNOWN', self.tick()['status'])
        self.lost = False
        self.assertEqual('OK', self.tick(now=1060)['status'])
        self.assertEqual(1, len([p for p, _ in self.calls if p == '/episode']))

    def test_two_absent_reads_then_same_body_retry_despite_tracker_change(self):
        self.lost = True
        self.tick()
        original = self.calls[0][1]
        self.lost = False
        self.present = False
        changed = [{**RECORD, 'title': 'Changed', 'updated_at': '2026-09-21T00:00:00Z'}]
        self.tick(changed, now=1060)
        self.tick(changed, now=1120)
        self.assertEqual(1, len([p for p, _ in self.calls if p == '/episode']))
        self.present = True
        result = self.tick(changed, now=1180)
        self.assertEqual(3, result['requests'])
        writes = [b for p, b in self.calls if p == '/episode']
        self.assertEqual([original, original], writes)
        self.assertEqual(1, result['backlog'])  # new version still needs delivery

    def test_pending_survives_task_close(self):
        self.lost = True
        self.tick()
        self.lost = False
        self.assertEqual(1, self.tick([], now=1060)['verified'])

    def test_poisoned_item_does_not_block_other_item(self):
        self.lost = True
        records = [RECORD, {**RECORD, 'id': 'proj-b'}]
        self.tick(records)
        self.lost = False
        self.present = False
        self.assertEqual('proj-a', self.tick(records, now=1060)['item'])
        self.present = True
        result = self.tick(records, now=1120)
        self.assertEqual('proj-b', result['item'])
        self.assertEqual(1, result['verified'])

    def test_pending_recovery_beats_large_fresh_backlog_without_repost(self):
        self.lost = True
        self.tick()
        self.lost = False
        records = [RECORD] + [{**RECORD, 'id': f'proj-new-{i}'} for i in range(250)]
        result = self.tick(records, now=1060)
        self.assertEqual('proj-a', result['item'])
        self.assertEqual(2, result['requests'])
        self.assertEqual(0, result['writes'])
        self.assertEqual(1, result['verified'])
        self.assertEqual(0, result['indeterminate'])

    def test_recovery_control_failure_does_not_starve_fresh_work(self):
        self.lost = True
        self.tick()
        self.lost = False
        self.control = False
        records = [RECORD, {**RECORD, 'id': 'proj-b'}]
        self.assertEqual('proj-a', self.tick(records, now=1060)['item'])
        self.control = True
        result = self.tick(records, now=1120)
        self.assertEqual('proj-b', result['item'])
        self.assertEqual('DEGRADED', result['status'])

    def test_controlled_retry_does_not_wait_behind_fresh_backlog(self):
        self.lost = True
        self.tick()
        original = self.calls[0][1]
        pending = self.state['items']['proj-a']['pending']
        pending['absent_reads'] = 2
        self.lost = False
        records = [RECORD] + [{**RECORD, 'id': f'proj-new-{i}'} for i in range(250)]
        result = self.tick(records, now=1060)
        self.assertEqual('proj-a', result['item'])
        self.assertEqual(1, result['writes'])
        self.assertEqual(original, self.calls[-3][1])
        self.assertEqual('OK', result['status'])

    def test_backoff_has_no_requests(self):
        self.tick()
        before = len(self.calls)
        self.assertEqual('BACKOFF', self.tick(now=1059)['mode'])
        self.assertEqual(before, len(self.calls))

    def test_existing_transition_is_not_starved_by_continuous_new_arrivals(self):
        self.tick()
        changed = {**RECORD, 'status': 'closed', 'trailing': True,
                   'updated_at': '2026-09-21T00:00:00Z'}
        records = [changed]
        served = []
        for offset in range(1, 5):
            records.append({**RECORD, 'id': f'proj-new-{offset}'})
            receipt = self.tick(records, now=1000 + offset * sync.INTERVAL)
            served.append(receipt['item'])
            self.assertLessEqual(receipt['writes'], 1)
            self.assertLessEqual(receipt['requests'], 3)
        self.assertEqual('proj-a', served[0])
        self.assertTrue(self.state['items']['proj-a']['final'])
        self.assertIn('proj-new-1', served)

    def test_indeterminate_receipt_degrades_during_other_success(self):
        self.lost = True
        records = [RECORD, {**RECORD, 'id': 'proj-b'}]
        self.tick(records)
        self.assertEqual('UNKNOWN', self.tick(now=1059)['status'])
        self.lost = False
        self.present = False
        self.assertEqual('UNKNOWN', self.tick(records, now=1060)['status'])
        self.present = True
        result = self.tick(records, now=1120)
        self.assertEqual(1, result['verified'])
        self.assertEqual('DEGRADED', result['status'])
        self.assertEqual(1, result['indeterminate'])

    def test_a_failed_recheck_is_retried_behind_a_standing_backlog(self):
        a = {**RECORD, 'id': 'proj-a'}
        self.tick([a])
        self.control = False
        failed = self.tick([a], now=1000 + sync.RECHECK)
        self.assertEqual('proj-a', failed['item'])
        self.assertIn('error', self.state['items']['proj-a'])
        self.control = True
        # A transition arrives on every tick, so the backlog never drains.
        records, served = [a], []
        for offset in range(1, 6):
            records.append({**RECORD, 'id': f'proj-new-{offset}'})
            receipt = self.tick(records, now=1000 + sync.RECHECK + offset * sync.INTERVAL)
            served.append(receipt['item'])
        self.assertIn('proj-a', served, 'a failed recheck must not starve behind arrivals')
        self.assertNotIn('error', self.state['items']['proj-a'])
        self.assertNotEqual('UNKNOWN', receipt['status'])

    def test_a_long_rotation_wait_is_not_counted_as_backlog_age(self):
        # aegis-64cr5o 2026-10-01: a recheck waited ~31h in rotation (by design),
        # then failed its read-back and went pending; oldest_seconds reported the
        # whole rotation wait and paged WorkItemIngressUnhealthy.
        self.tick()
        late = 1000 + 30 * 3600
        self.state['items']['proj-a']['due_since'] = 1000 + sync.RECHECK  # a stale stamp
        self.present = False
        receipt = self.tick(now=late)
        self.assertIn('pending', self.state['items']['proj-a'])
        self.assertLess(receipt['oldest_seconds'], sync.INTERVAL * 2, receipt)

    def test_a_pure_recheck_carries_no_backlog_clock(self):
        self.tick()
        self.state['items']['proj-a']['due_since'] = 1000  # stale stamp from before
        # proj-a is a due RECHECK; the new arrival proj-b is owed and is served first.
        receipt = self.tick([RECORD, {**RECORD, 'id': 'proj-b'}], now=1000 + sync.RECHECK)
        self.assertEqual(receipt['item'], 'proj-b')
        self.assertNotIn('due_since', self.state['items']['proj-a'])  # stale stamp cleared

    def test_recheck_staleness_and_utilization_are_reported(self):
        # aegis-7dcleu: with due_since owed-only, a starving recheck needs its own
        # signal. Oldest verification among current items, and demand/capacity.
        self.tick()
        receipt = self.tick(now=1000 + 7200)
        self.assertEqual(receipt['oldest_verified_seconds'], 7200)
        self.assertEqual(receipt['recheck_utilization'], 0.0007)  # 1 item / 24h / 60 per h
        self.assertEqual(sync.RECHECK, 24 * 3600)
        self.assertEqual(receipt['unverified_items'], 0)

    def test_utilization_at_the_measured_population_is_a_pinned_literal(self):
        # 645 current items (measured 2026-10-01): a literal, not the formula mirrored.
        records = [{**RECORD, 'id': f'proj-{n}'} for n in range(645)]
        receipt = self.tick(records)
        self.assertEqual(receipt['recheck_utilization'], 0.4479)

    def test_zero_coverage_is_visible_not_fresh(self):
        # Nothing verified: oldest_verified_seconds reads 0, which looks fresh, so
        # unverified_items must carry the signal (malcolm, review of #60).
        self.control = False
        receipt = self.tick([RECORD, {**RECORD, 'id': 'proj-b'}])
        self.assertEqual(receipt['oldest_verified_seconds'], 0)
        self.assertEqual(receipt['unverified_items'], 2)

    def test_successful_version_not_reposted_and_recheck_is_read_only(self):
        self.tick()
        self.assertEqual(0, self.tick(now=1060)['requests'])
        self.assertEqual(2, self.tick(now=1000 + sync.RECHECK)['requests'])
        self.assertEqual(1, len([p for p, _ in self.calls if p == '/episode']))

    def test_failed_periodic_control_stays_visible_during_other_success(self):
        self.tick()
        self.control = False
        self.tick(now=1000 + sync.RECHECK)
        records = [RECORD, {**RECORD, 'id': 'proj-b'}]
        # The failed recheck is owed work (aegis-alfe2l), so it is retried
        # first; it fails again and must stay visible while proj-b succeeds.
        self.assertEqual('proj-a', self.tick(records, now=1060 + sync.RECHECK)['item'])
        self.control = True
        result = self.tick(records, now=1120 + sync.RECHECK)
        self.assertEqual('proj-b', result['item'])
        self.assertEqual(1, result['verified'])
        self.assertEqual('UNKNOWN', result['status'])
        self.assertEqual(1, result['backlog'])

    def test_retraction_does_not_repost_in_same_tick(self):
        self.tick()
        self.present = False
        result = self.tick(now=1000 + sync.RECHECK)
        self.assertEqual(2, result['requests'])
        self.assertEqual(0, result['writes'])
        self.assertEqual('UNKNOWN', result['status'])

    def test_retry_attempt_cap(self):
        self.present = False
        self.tick()
        for now in range(1060, 4000, 60):
            result = self.tick(now=now)
            self.assertLessEqual(result['requests'], 3)
        self.assertEqual(3, len([p for p, _ in self.calls if p == '/episode']))
        self.assertIn('pending', self.state['items']['proj-a'])

    def test_unknown_query_format_is_not_absence(self):
        with patch.object(self, 'post', return_value={}):
            self.assertEqual('UNKNOWN', self.tick()['status'])
        self.assertEqual(0, self.state['items']['proj-a']['pending']['absent_reads'])

    def test_bounds_and_unsafe_ids_before_network(self):
        for record in ({**RECORD, 'id': 'unsafe/>}'}, {**RECORD, 'title': 'x' * sync.MAX_BODY}):
            self.state = {}
            result = self.tick([record])
            self.assertEqual('UNKNOWN', result['status'])
            self.assertEqual(1, result['invalid'])
        self.assertEqual([], self.calls)

    def test_malformed_record_does_not_block_good_record(self):
        result = self.tick([{**RECORD, 'id': 'proj-bad', 'title': ''}, RECORD])
        self.assertEqual(1, result['verified'])
        self.assertEqual(1, result['invalid'])
        self.assertEqual('UNKNOWN', result['status'])

    def test_stale_backlog_fails_even_with_successful_item(self):
        records = [RECORD, {**RECORD, 'id': 'proj-b'}]
        self.tick(records)
        self.assertEqual('OK', self.tick(records, now=2000)['status'])
        # A permanently unresolved item remains visible even during its backoff.
        self.state['items']['proj-a']['pending'] = {
            'body': self.calls[0][1], 'attempts': 3, 'absent_reads': 2}
        self.state['items']['proj-a'].update(due_since=1000, not_before=10000)
        self.assertEqual('UNKNOWN', self.tick(records, now=2060)['status'])


class TrackerFormats(unittest.TestCase):
    def test_old_list_and_complete_envelope(self):
        self.assertEqual([RECORD], sync.records_from([RECORD]))
        self.assertEqual([RECORD], sync.records_from({'issues': [RECORD], 'total': 1,
                                                    'offset': 0, 'has_more': False}))

    def test_truncation_error_and_ambiguous_formats_refused(self):
        for value in ({}, {'issues': [RECORD], 'total': 2, 'offset': 0, 'has_more': False},
                      {'issues': [RECORD], 'total': 1, 'offset': 1, 'has_more': False},
                      {'issues': [RECORD], 'total': 1, 'offset': 0, 'has_more': True},
                      [RECORD, RECORD], None):
            with self.assertRaises((ValueError, KeyError)):
                sync.records_from(value)

    def test_scope_current_assigned_including_blocked_not_closed_or_unassigned(self):
        variants = [RECORD, {**RECORD, 'id': 'proj-open', 'status': 'open'},
                    {**RECORD, 'id': 'proj-blocked', 'status': 'blocked'},
                    {**RECORD, 'id': 'proj-closed', 'status': 'closed'},
                    {**RECORD, 'id': 'proj-unassigned', 'status': 'open', 'assignee': None}]
        self.assertEqual(['proj-a', 'proj-open', 'proj-blocked'],
                         [r['id'] for r in sync.records_from(variants)])

    def test_public_cli_explicit_authority_no_export(self):
        with patch.object(sync.subprocess, 'run') as run:
            run.return_value.stdout = json.dumps([RECORD])
            sync.collect(Path('/tmp/fixture.db'))
        argv = run.call_args.args[0]
        self.assertEqual(['br', '--db', '/tmp/fixture.db', 'list'], argv[:4])
        self.assertIn('--deferred', argv)
        self.assertEqual('0', argv[argv.index('--limit') + 1])
        # aegis-prhnn4: `--deferred` alone added 0 of 156 deferred rows beside
        # explicit --status filters. The status must be requested by name.
        statuses = {argv[i + 1] for i, a in enumerate(argv) if a == '--status'}
        self.assertEqual({'open', 'in_progress', 'blocked', 'deferred'}, statuses)

    def test_scope_includes_assigned_deferred_work_only(self):
        # aegis-prhnn4: the probe doc promises deferred ASSIGNMENTS are covered.
        variants = [{**RECORD, 'id': 'proj-def', 'status': 'deferred'},
                    {**RECORD, 'id': 'proj-def-free', 'status': 'deferred', 'assignee': None}]
        self.assertEqual(['proj-def'], [r['id'] for r in sync.records_from(variants)])


class Transitions(unittest.TestCase):
    """aegis-3b3nrb: closes and blocker changes reach the graph, promptly."""

    def fake_br(self, current, by_id):
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            out = type('R', (), {})()
            if '--id' in argv:
                wanted = [argv[i + 1] for i, a in enumerate(argv) if a == '--id']
                out.stdout = json.dumps([by_id[i] for i in wanted if i in by_id])
            else:
                out.stdout = json.dumps(current)
            return out
        return run, calls

    def test_a_tracked_item_that_closed_is_fetched_as_trailing(self):
        closed = {**RECORD, 'id': 'proj-done', 'status': 'closed', 'closed_at': '2026-09-24T04:16:21Z'}
        run, calls = self.fake_br([RECORD], {'proj-done': closed})
        records = sync.collect(Path('/tmp/f.db'), tracked=['proj-a', 'proj-done'], run=run)
        got = {r['id']: r for r in records}
        self.assertEqual('closed', got['proj-done']['status'])
        self.assertTrue(got['proj-done']['trailing'])
        self.assertNotIn('trailing', got['proj-a'])
        self.assertTrue(any('--all' in c for c in calls), 'closed records need --all')

    def test_an_unassigned_blocker_of_current_work_is_fetched(self):
        blocker = {**RECORD, 'id': 'proj-dep', 'status': 'open', 'assignee': None}
        run, _ = self.fake_br([{**RECORD, 'blocked_on': ['proj-dep']}], {'proj-dep': blocker})
        with patch('ingest_work_items.attach_blocked_on', lambda records, db: []):
            records = sync.collect(Path('/tmp/f.db'), run=run)
        self.assertIn('proj-dep', {r['id'] for r in records})

    def test_a_change_goes_ahead_of_the_recheck_rotation(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / 'state.json'
        a, b = {**RECORD, 'id': 'proj-a'}, {**RECORD, 'id': 'proj-b'}
        post = lambda endpoint, body: ({'outcome': 'created'} if endpoint == '/episode'
                                       else {'rows': [{'s': 'c', 't': 'WorkItem', 'o': 'Observation'}]})
        state = {}
        for now in (1000, 1060):  # both written and verified
            sync.tick([a, b], state, path, actor='t', source='br:x', now=now, post=post)
        # b changes; a has the OLDER last_attempt and is due for its recheck
        b2 = {**b, 'status': 'closed', 'updated_at': '2026-09-25T00:00:00Z'}
        later = 1060 + sync.RECHECK + 120
        receipt = sync.tick([a, b2], state, path, actor='t', source='br:x', now=later, post=post)
        self.assertEqual('proj-b', receipt['item'], 'the transition must not wait behind a recheck')

    def test_a_closed_trailing_item_is_final_once_verified(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / 'state.json'
        done = {**RECORD, 'id': 'proj-done', 'status': 'closed', 'trailing': True}
        post = lambda endpoint, body: ({'outcome': 'created'} if endpoint == '/episode'
                                       else {'rows': [{'s': 'c', 't': 'WorkItem', 'o': 'Observation'}]})
        state = {}
        sync.tick([done], state, path, actor='t', source='br:x', now=1000, post=post)
        self.assertTrue(state['items']['proj-done'].get('final'))

    def test_a_tracked_deferred_item_that_closes_ends_closed(self):
        # aegis-prhnn4 [prhnn4-close-gap] (dearing): a dep-less deferred bead that
        # closed kept observedStatus=deferred forever, because the sync never
        # tracked it and the backfill re-projects a covered bead only when it has
        # blocked_on. Once deferred work is current, its close trails out.
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / 'state.json'
        deferred = {**RECORD, 'id': 'proj-def', 'status': 'deferred'}
        closed = {**deferred, 'status': 'closed', 'closed_at': '2026-10-01T05:00:00Z',
                  'updated_at': '2026-10-01T05:00:00Z'}
        bodies = []

        def post(endpoint, body):
            if endpoint == '/episode':
                bodies.append(body)
                return {'outcome': 'created'}
            return {'rows': [{'s': 'c', 't': 'WorkItem', 'o': 'Observation'}]}
        state = {}
        run, _ = self.fake_br([deferred], {})
        records = sync.collect(Path('/tmp/f.db'), run=run)
        self.assertEqual(['proj-def'], [r['id'] for r in records], 'deferred work is current')
        sync.tick(records, state, path, actor='t', source='br:x', now=1000, post=post)
        self.assertIn('proj-def', state['items'], 'the sync now tracks it')
        # It closes: it leaves the current set and trails out through `tracked`.
        run, _ = self.fake_br([], {'proj-def': closed})
        records = sync.collect(Path('/tmp/f.db'), tracked=list(state['items']), run=run)
        self.assertTrue(records[0]['trailing'])
        sync.tick(records, state, path, actor='t', source='br:x', now=1060, post=post)
        self.assertTrue(state['items']['proj-def'].get('final'))
        last = json.dumps(bodies[-1])
        self.assertIn('closed', last, 'the final observation is closed')
        self.assertNotIn('"deferred"', last)

    def test_dependencies_are_looked_up_only_when_updated_at_moves(self):
        looked = []

        def fake_attach(records, db):
            for r in records:
                looked.append(r['id'])
                r['blocked_on'] = ['proj-dep']
        a = {**RECORD, 'dependency_count': 1, 'updated_at': 't1'}
        cache = {}
        with patch('ingest_work_items.attach_blocked_on', fake_attach):
            sync.attach_deps([dict(a)], None, cache)
            again = dict(a)
            sync.attach_deps([again], None, cache)          # same updated_at: cached
            sync.attach_deps([{**a, 'updated_at': 't2'}], None, cache)  # moved: looked up
        self.assertEqual(['proj-a', 'proj-a'], looked)
        self.assertEqual(['proj-dep'], again['blocked_on'])


if __name__ == '__main__':
    unittest.main()


class TrackerReadBound(unittest.TestCase):
    """aegis-ky3zpa: a tracker read under a crew write burst took 64.5 s, and a
    15 s bound turned every minute of the burst into UNKNOWN."""

    MEASURED_TAIL_S = 64.5

    def reads(self):
        seen = []

        def run(argv, **kw):
            seen.append(kw.get('timeout'))
            return type('R', (), {'stdout': json.dumps([])})()
        sync.collect(Path('x.db'), tracked=['proj-gone'], run=run, deps_cache={})
        return seen

    def test_every_tracker_read_outlasts_the_measured_tail(self):
        timeouts = self.reads()
        self.assertEqual(len(timeouts), 2, 'current-work read and tracked-id lookup')
        for timeout in timeouts:
            self.assertGreater(timeout, self.MEASURED_TAIL_S)
