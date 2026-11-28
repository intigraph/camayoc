"""One Directive's unreadable deadline is a per-item UNKNOWN, not a failed batch (aegis-b9k)."""
import datetime as dt
import unittest

from test_directive_edges import board, NOW, A
import change_adapter as ca
import directive_deadline as dd
import directive_edges as de


def row(when):
    return {'due': when if isinstance(when, str) else when.isoformat(), 'datatype': dd.XSD_DATETIME}


def post_with(deadlines):
    """deadlines: {entity: (root_rows, declared_rows)}; others have none."""
    base = board()
    def post(endpoint, body):
        q = body.get('query', '')
        if 'traceabilityDueAt' in q:
            for entity, (root, declared) in deadlines.items():
                if f'<{A}{entity}>' in q:
                    return {'rows': root if body.get('graph') in (None, dd.ROOT_GRAPH) else declared}
            return {'rows': []}
        return base(endpoint, body)
    return post


class Isolation(unittest.TestCase):
    def test_conflicting_item_keeps_its_verdict_and_rest_is_judged(self):
        # Review finding 1: the proposer view keeps the verdict judge() knows.
        post = post_with({'new': ([row(NOW)], [row(NOW + dt.timedelta(days=1))])})
        plain = de.judge(board(), 'new', NOW)
        out = ca._directive('directive', post, {'items': ['new', 'old']}, NOW, NOW.timestamp())
        by = {r['entity']: r for r in out['records']}
        self.assertEqual(set(by), {'new', 'old'})
        want = de.UNTRACED if plain['verdict'] == de.PROPOSED else plain['verdict']
        self.assertNotEqual(want, de.TRACED)
        self.assertEqual(by['new']['verdict'], want)
        self.assertEqual(by['new']['event_id'], de.event_id('new'))
        self.assertIn('conflicting traceability deadlines', by['new']['evidence'])
        self.assertEqual(by['old']['verdict'], de.TRACED)

    def test_conflicting_item_lapses_now_and_rest_is_judged(self):
        # Review finding 2: no UNKNOWN reaches the lapse emitter; the item
        # reaches a person now (like an unanchored one) with the reason.
        post = post_with({'new': ([row(NOW)], [row(NOW + dt.timedelta(days=1))])})
        out = ca._directive('directive-lapse', post, {'items': ['new', 'old']}, NOW, NOW.timestamp())
        by = {r['entity']: r for r in out['records']}
        self.assertEqual(set(by), {'new', 'old'})
        self.assertEqual(by['new']['verdict'], de.LAPSED)
        self.assertTrue(by['new']['event_id'])
        self.assertIsNone(by['new']['due_at'])
        self.assertFalse(by['new']['explicit_deadline'])
        self.assertIn('conflicting traceability deadlines', by['new']['evidence'])
        self.assertEqual(by['old']['verdict'], de.NOT_LAPSED)
        verdicts = {r['verdict'] for r in out['records']}
        self.assertLessEqual(verdicts, {de.LAPSED, de.NOT_LAPSED})

    def test_unreadable_lapse_event_is_stable_and_distinct(self):
        post = post_with({'new': ([row(NOW)], [row(NOW + dt.timedelta(days=1))])})
        a = de.judge(post, 'new', NOW, isolate_deadline=True)
        b = de.judge(post, 'new', NOW + dt.timedelta(hours=1), isolate_deadline=True)
        self.assertEqual(a['lapse_event_id'], b['lapse_event_id'])
        self.assertNotEqual(a['lapse_event_id'], de.event_id(f"new\0{a['verdict']}\0unanchored"))

    def test_full_run_survives_a_conflicting_item(self):
        post = post_with({'new': ([row(NOW), row(NOW + dt.timedelta(days=1))], [])})
        for kind in ('directive', 'directive-lapse'):
            with self.subTest(kind=kind):
                out = ca._directive(kind, post, {'full': True}, NOW, NOW.timestamp())
                by = {r['entity']: r for r in out['records']}
                self.assertIn('new', by)
                self.assertIn('deadline UNKNOWN', by['new']['evidence'])
                self.assertNotIn('UNKNOWN', {r['verdict'] for r in out['records']})

    def test_one_instant_spelled_twice_is_not_a_conflict(self):
        post = post_with({'new': ([row('2026-10-08T00:00:00Z')], [row('2026-10-08T00:00:00+00:00')])})
        r = de.judge(post, 'new', NOW)
        self.assertTrue(r['explicit_deadline'])
        self.assertEqual(r['lapse'], de.NOT_LAPSED)

    def test_judge_itself_still_refuses_a_conflict(self):
        post = post_with({'new': ([row(NOW)], [row(NOW + dt.timedelta(days=1))])})
        with self.assertRaises(dd.DeadlineUnknown):
            de.judge(post, 'new', NOW)

    def test_failed_read_still_fails_the_batch(self):
        base = board()
        def post(endpoint, body):
            if 'traceabilityDueAt' in body.get('query', ''):
                raise ConnectionError('store unreachable')
            return base(endpoint, body)
        with self.assertRaises(ConnectionError):
            ca._directive('directive', post, {'items': ['new']}, NOW, NOW.timestamp())


if __name__ == '__main__':
    unittest.main()
