"""aegis-j3q2: a never-verified candidate whose seed is archived needs a POSITIVE archive proof."""
import unittest

import test_seed_work_item_sync as fixture
from test_seed_change_custody import page
from test_seed_work_items import GRAPH, ID, rows
import seed_change_custody as sc
import seed_work_item_sync as sync
import seed_work_items as nw


class Archived(unittest.TestCase):
    setUp = fixture.Tick.setUp
    tearDown = fixture.Tick.tearDown

    def serve(self, *, live=False, archive_answer=None, projected=False, native=None):
        """Native read empty unless live; archive ASK answers as given;
        projection absence (prove_absent) answers per `projected`."""
        self.queries = []
        def post(endpoint, body, **kwargs):
            if endpoint == '/knot':
                self.writes += 1
                return {'replaced': True, 'tx_id': 99, 'conforms': True}
            query = body['query']
            self.queries.append(query)
            if 'seeds-archive' in query:
                if isinstance(archive_answer, Exception):
                    raise archive_answer
                return archive_answer
            if 'BIND(' in query:
                if native is not None:
                    if isinstance(native, Exception):
                        raise native
                    return native
                return {'rows': rows() if live else [], 'truncated': False}
            if query.startswith('ASK') and '?p ?o' in query:
                return {'result': projected, 'truncated': False}
            return {'rows': [], 'truncated': False}
        return post

    def tick(self, post):
        return sync.tick(self.reader, self.j, post,
                         changes=lambda **args: page() if args['since'] < 10 else
                         page(records=[], next_tx=args['since'], watermark_tx=args['since']),
                         plane='urn:private:knowledge', actor='fixture')

    def test_archive_graph_follows_the_seeds_contract(self):
        self.assertEqual(nw.Board('https://seeds.local/project/aegis', 'aegis').archive_graph,
                         'https://seeds.local/project/aegis#seeds-archive')
        self.assertEqual(nw.Board('https://x.test/g#frag', 'demo').archive_graph,
                         'https://x.test/g#frag-seeds-archive')

    def test_positive_archive_proof_drops_the_candidate_without_any_write(self):
        post = self.serve(archive_answer={'result': True, 'truncated': False})
        receipt = self.tick(post)
        self.assertEqual((receipt['status'], receipt['writes'], receipt.get('archived')), ('OK', 0, 1))
        self.assertIsNone(self.j.next())
        self.assertEqual(self.writes, 0)
        archive_ask, = [q for q in self.queries if 'seeds-archive' in q]
        self.assertIn(f'GRAPH <{GRAPH}#seeds-archive>', archive_ask)
        self.assertIn(f'FILTER NOT EXISTS {{ GRAPH <{GRAPH}>', archive_ask)

    def test_empty_live_read_without_positive_archive_proof_stays_unknown(self):
        for answer in ({'result': False, 'truncated': False}, {'result': True, 'truncated': True},
                       {'result': 'true'}, {'rows': []}, ['x'], None, OSError('down')):
            with self.subTest(answer=answer):
                self.setUp()
                try:
                    receipt = self.tick(self.serve(archive_answer=answer))
                    self.assertEqual(receipt['status'], 'UNKNOWN')
                    self.assertNotIn('archived', receipt)
                    self.assertEqual(self.j.next()[0], ID)  # candidate retained
                finally:
                    self.tearDown()

    def test_verified_projection_of_an_archived_seed_is_never_removed(self):
        self.j.retain(page())
        self.j.db.execute('INSERT INTO verified VALUES(?,?)', (ID, 2))
        self.j.db.commit()
        receipt = self.tick(self.serve(archive_answer={'result': True, 'truncated': False}))
        self.assertEqual(receipt['status'], 'UNKNOWN')
        self.assertEqual(self.j.next()[0], ID)
        self.assertEqual(self.j.db.execute('SELECT version FROM verified WHERE id=?', (ID,)).fetchone(), (2,))
        self.assertFalse([q for q in self.queries if 'seeds-archive' in q])  # not even asked

    def test_live_seed_is_projected_normally_and_never_asks_the_archive(self):
        receipt = self.tick(self.serve(live=True, archive_answer={'result': True}))
        self.assertNotIn('archived', receipt)
        self.assertEqual(self.writes, 1)
        self.assertFalse([q for q in self.queries if 'seeds-archive' in q])

    def test_never_verified_but_projected_seed_is_never_dropped(self):
        # malcolm review: never-verified is not never-projected (we0zuubl1).
        receipt = self.tick(self.serve(archive_answer={'result': True, 'truncated': False},
                                       projected=True))
        self.assertEqual(receipt['status'], 'UNKNOWN')
        self.assertNotIn('archived', receipt)
        self.assertEqual(self.j.next()[0], ID)

    def test_only_a_complete_empty_live_read_consults_the_archive(self):
        # malcolm review: oversize, wrong identity, truncation and transport
        # failures are not a missing record and never reach the archive.
        wrong = rows()
        for row in wrong:
            row['s'] = nw.ITEM + 'demo-other'
        for native in ({'rows': wrong, 'truncated': False}, {'rows': [], 'truncated': True},
                       {'rows': None}, {'rows': [{}] * 200, 'truncated': False},
                       RuntimeError('HTTP 403 TOKEN-CANARY')):
            with self.subTest(native=type(native).__name__):
                self.setUp()
                try:
                    receipt = self.tick(self.serve(native=native,
                                                   archive_answer={'result': True, 'truncated': False}))
                    self.assertEqual(receipt['status'], 'UNKNOWN')
                    self.assertFalse([q for q in self.queries if 'seeds-archive' in q])
                    self.assertEqual(self.j.next()[0], ID)
                finally:
                    self.tearDown()

    def test_journal_drop_refuses_prepared_changed_retired_or_verified_rows(self):
        self.j.retain(page())
        with self.assertRaises(sc.CustodyUnknown):
            self.j.drop_archived(ID, 11)  # changed candidate
        self.j.prepare(ID, 10, {'exact': 'body'})
        with self.assertRaises(sc.CustodyUnknown):
            self.j.drop_archived(ID, 10)  # holds a stored effect
        self.assertIsNotNone(self.j.next()[2])


if __name__ == '__main__':
    unittest.main()
