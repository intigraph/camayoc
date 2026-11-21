"""Explicit deadlines reach the existing administrator lapse alert, never a model early."""
import datetime as dt
import unittest

from test_directive_edges import board, NOW, A
import directive_edges as de
import directive_deadline as dd
import change_adapter as ca


class ExplicitDeadline(unittest.TestCase):
    def post(self, rows):
        base = board()
        def post(endpoint, body):
            if 'traceabilityDueAt' in body.get('query', ''):
                return {'rows': rows if body.get('graph') in (None, dd.ROOT_GRAPH) else []}
            return base(endpoint, body)
        return post

    def row(self, date):
        return {'due': date, 'datatype': dd.XSD_DATETIME}

    def test_future_deadline_suppresses_lapse_and_schedules_exact_wake(self):
        when = NOW + dt.timedelta(days=14)
        post = self.post([self.row(when.isoformat())])
        result = de.judge(post, 'new', NOW)
        self.assertEqual(result['lapse'], de.NOT_LAPSED)
        self.assertEqual(ca.next_check(post, 'directive-lapse', result, NOW.timestamp()), when.timestamp())
        # Never invoke a model or query candidate policies before evaluation is due.
        class NoModel:
            def choice(self, *args):
                raise AssertionError('early model call')
        self.assertEqual(de.plan(post, NoModel(), 'new', NOW)['verdict'], 'DEFERRED')

    def test_current_typed_date_cell_preserves_lexical_value(self):
        row = self.row(NOW.isoformat())
        row['due'] = {'value': NOW.isoformat(), 'datatype': dd.XSD_DATETIME}
        self.assertEqual(de.judge(self.post([row]), 'new', NOW)['lapse'], de.LAPSED)

    def test_deadline_boundary_and_overdue_reach_person(self):
        for date in (NOW, NOW-dt.timedelta(days=3)):
            result = de.judge(self.post([self.row(date.isoformat())]), 'new', NOW)
            self.assertEqual(result['lapse'], de.LAPSED)
            self.assertIsNotNone(result['lapse_event_id'])

    def test_invalid_or_ambiguous_deadline_is_unknown_not_grace_fallback(self):
        for rows in ([self.row('later')], [dict(due=NOW.isoformat(), datatype='string')],
                     [self.row(NOW.isoformat()), self.row((NOW+dt.timedelta(days=1)).isoformat())],
                     [self.row(NOW.isoformat())]*3):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                de.judge(self.post(rows), 'new', NOW)

    def test_dangling_links_do_not_hide_due_directive(self):
        base = board()
        base.add(A+'new', A+'trackedBy', A+'untyped-work')
        base.add(A+'new', A+'governedBy', A+'untyped-policy')
        def post(endpoint, body):
            if 'traceabilityDueAt' in body.get('query', ''):
                return {'rows': [self.row(NOW.isoformat())] if body.get('graph') in (None, dd.ROOT_GRAPH) else []}
            return base(endpoint, body)
        result = de.judge(post, 'new', NOW)
        self.assertEqual(result['verdict'], de.UNTRACED)
        self.assertEqual(result['lapse'], de.LAPSED)

    def test_truncated_read_is_unknown(self):
        def post(endpoint, body):
            if 'traceabilityDueAt' in body.get('query', ''):
                return {'rows': [], 'truncated': True}
            return board()(endpoint, body)
        with self.assertRaises(ValueError):
            de.judge(post, 'new', NOW)

    def test_subscribed_to_both_spellings_and_routes_changed_subject(self):
        description = ca.description('directive-lapse')
        self.assertTrue({dd.STABLE, dd.LEGACY} <= set(description['attributes']))
        for prop in (dd.STABLE, dd.LEGACY):
            self.assertEqual(ca.targets(None, 'directive-lapse', [{'entity': A+'new', 'attribute': prop}], []), ['new'])
