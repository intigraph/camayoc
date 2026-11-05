"""The review consumer sees memory obligations without promoting inferred facts."""
import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import blocked_by as bb
import change_adapter as ca
import planes
import review_due as rd
from sparql_fake import TripleStore

NOW = dt.datetime(2026, 11, 4, tzinfo=dt.timezone.utc)


class MemoryTriples(TripleStore):
    def _term(self, tok):
        if tok.startswith('<') and tok.endswith('>') and not tok.startswith((f'<{self.a}', f'<{self.q}', f'<{rd.TYPE}>')):
            return ('iri', tok[1:-1])
        return super()._term(tok)


class Store:
    def __init__(self, inferred, root=()):
        self.inferred = list(inferred)
        self.root = list(root)

    def post(self, endpoint, body):
        graph = body.get('graph')
        triples = self.inferred if graph == planes.plane_for('inferred') else self.root if graph is None else []
        return {'rows': MemoryTriples(triples, bb.A, bb.Q, {'naturalKey', 'supersededBy'}).select(body['query']),
                'truncated': False}


def memory(subject='memory'):
    return [(subject, 'a', 'Preference'), (subject, 'naturalKey', 'key'),
            (subject, 'reviewAfter', '2026-10-01T00:00:00Z')]


class InferredMemory(unittest.TestCase):
    def test_full_and_incremental_due_but_nonmemory_unchanged(self):
        store = Store(memory() + [('fact', 'reviewAfter', '2026-10-01T00:00:00Z')])
        full = rd.evaluate(store.post, now=NOW)
        self.assertEqual([(r['entity'], r['verdict']) for r in full], [('memory', 'DUE')])
        discovery = ca.evaluate('review', store.post, {'discover': True, 'now': NOW.timestamp()})
        self.assertEqual(discovery['items'], ['memory'])
        incremental = ca.evaluate('review', store.post, {'items': ['memory', 'fact'], 'now': NOW.timestamp()})
        self.assertEqual(incremental['records'], full)
        self.assertNotIn(planes.plane_for('inferred'), bb.graphs())
        self.assertIn(planes.plane_for('inferred'), ca.description('review')['graphs'])
        self.assertNotIn(planes.plane_for('inferred'), ca.description('blocked')['graphs'])

    def test_nonmemory_twin_does_not_change_root_age(self):
        store = Store([('f', 'reviewAfter', '2026-01-01T00:00:00Z')],
                      [('f', 'reviewAfter', '2027-01-01T00:00:00Z')])
        self.assertEqual(rd.evaluate(store.post, now=NOW)[0]['verdict'], 'NOT_DUE')

    def test_future_deadline_and_supersession_resolve(self):
        store = Store(memory())
        store.inferred[-1] = ('memory', 'reviewAfter', '2026-11-05T00:00:00Z')
        first = ca.evaluate('review', store.post, {'items': ['memory'], 'now': NOW.timestamp()})
        deadline = first['next_checks']['memory']
        self.assertEqual(deadline, NOW.timestamp() + 86400)
        self.assertEqual(ca.evaluate('review', store.post, {'items': ['memory'], 'now': deadline})['records'][0]['verdict'], 'DUE')
        store.inferred.append(('memory', 'supersededBy', 'newer'))
        result = ca.evaluate('review', store.post, {'items': ['memory'], 'now': deadline})
        self.assertEqual(result['records'][0]['verdict'], 'NOT_DUE')
        self.assertIsNone(result['next_checks']['memory'])

    def test_canonical_and_mixed_terms_match_legacy(self):
        expected = rd.evaluate(Store(memory()).post, now=NOW)
        for class_iri in [rd.MEMORY_CORE + 'Preference']:
            for canonical_key in [False, True]:
                key = rd.MEMORY_CORE + 'naturalKey' if canonical_key else 'naturalKey'
                store = Store([('memory', 'a', class_iri), ('memory', key, 'key'),
                               ('memory', rd.MEMORY_CORE + 'reviewAfter', '2026-10-01T00:00:00Z')])
                self.assertEqual(rd.evaluate(store.post, now=NOW), expected)

    def test_bad_inferred_read_is_not_empty_success(self):
        store = Store(memory())
        for response in [{'error': 'timeout'}, {'rows': [], 'truncated': True}]:
            def post(endpoint, body):
                return response if body.get('graph') == planes.plane_for('inferred') else store.post(endpoint, body)
            with self.assertRaises(ValueError):
                rd.evaluate(post, now=NOW)

    def test_runtime_aliases_match_exact_ledger_pairs(self):
        from build_stable_namespaces import combined_ledger
        pairs = combined_ledger(Path(__file__).resolve().parents[1])['pairs']
        for pair in pairs:
            if pair['old'] in rd.MEMORY_CLASSES:
                self.assertIn(pair['new'], rd.MEMORY_CLASSES)
            for name, iris in rd.MEMORY_PROPERTIES.items():
                if pair['old'] == bb.A + name:
                    self.assertIn(pair['new'], iris)

    def test_type_and_key_both_required(self):
        store = Store([('class', 'a', 'Preference'), ('key', 'naturalKey', 'key'),
                       ('class', 'reviewAfter', '2026-01-01'), ('key', 'reviewAfter', '2026-01-01')])
        self.assertEqual(rd.evaluate(store.post, now=NOW), [])


if __name__ == '__main__':
    unittest.main()
