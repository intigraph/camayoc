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
    def test_conflicting_item_is_unknown_and_rest_is_judged(self):
        post = post_with({'new': ([row(NOW)], [row(NOW + dt.timedelta(days=1))])})
        for kind in ('directive', 'directive-lapse'):
            with self.subTest(kind=kind):
                out = ca._directive(kind, post, {'items': ['new', 'old']}, NOW, NOW.timestamp())
                by = {r['entity']: r for r in out['records']}
                self.assertEqual(set(by), {'new', 'old'})
                self.assertEqual(by['new']['verdict'], 'UNKNOWN')
                self.assertIsNone(by['new']['event_id'])
                self.assertIn('conflicting traceability deadlines', by['new']['evidence'])
                self.assertEqual(by['old']['verdict'], 'TRACED' if kind == 'directive' else 'NOT_LAPSED')
                self.assertIsNone(out['next_checks']['new'])

    def test_full_run_survives_a_conflicting_item(self):
        post = post_with({'new': ([row(NOW), row(NOW + dt.timedelta(days=1))], [])})
        out = ca._directive('directive', post, {'full': True}, NOW, NOW.timestamp())
        self.assertIn('UNKNOWN', {r['verdict'] for r in out['records'] if r['entity'] == 'new'})
        self.assertTrue(any(r['verdict'] != 'UNKNOWN' for r in out['records']))

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
