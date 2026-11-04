import json
import tempfile
import unittest
from pathlib import Path

from test_seed_change_custody import PEER, page
from test_seed_work_items import GRAPH, ID
import release_unconfirmed_work_item as tool
import seed_change_custody as sc
import seed_work_item_sync as sync
import seed_work_items as nw


def server(*answers):
    """Fake /query that returns the given answers in order and records queries."""
    calls = []
    def post(route, body):
        calls.append((route, body['query']))
        answer = answers[len(calls) - 1]
        if isinstance(answer, Exception):
            raise answer
        return answer
    return post, calls


ABSENT = {'result': False, 'truncated': False}


class ReleaseUnconfirmed(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'journal.sqlite3'
        self.board = nw.Board(GRAPH, 'demo')
        self.j = sc.Journal(self.path, self.board, server=PEER, initial_tx=9)
        self.j.retain(page())
        self.j.prepare(ID, 10, {'exact': 'body'})
        self.body = self.j.db.execute('SELECT body FROM pending WHERE id=?', (ID,)).fetchone()[0]

    def tearDown(self):
        self.j.db.close()
        self.tmp.cleanup()

    def dump(self):
        return list(self.j.db.iterdump())

    def test_absent_unconfirmed_effect_becomes_a_fresh_candidate(self):
        post, calls = server(ABSENT, ABSENT)
        receipt = tool.release(self.j, post, ID)
        self.assertEqual(receipt, {'status': 'RELEASED', 'item': ID, 'tx': 10})
        # Both every named graph and the default graph were asked about the subject.
        self.assertEqual([q.startswith('ASK { GRAPH ?g') for _, q in calls], [True, False])
        # Fresh candidate: the next delivery prepares and writes once, as for new work.
        self.assertEqual(self.j.next(), (ID, 10, None))
        self.j.prepare(ID, 10, {'new': 'effect'})

    def test_present_subject_is_refused_and_nothing_changes(self):
        before = self.dump()
        for answers in ((ABSENT, {'result': True}), ({'result': True},),
                        (ABSENT, {'result': False, 'truncated': True}),
                        ({'result': None},), (OSError('down'),), (['not', 'an', 'object'],)):
            with self.subTest(answers=answers):
                post, _ = server(*answers)
                receipt = tool.release(self.j, post, ID)
                self.assertEqual(receipt['status'], 'REFUSED')
                self.assertEqual(self.dump(), before)

    def test_verified_or_unprepared_rows_are_refused(self):
        self.j.acknowledge(ID, 10, 3)
        before = self.dump()
        post, calls = server(ABSENT, ABSENT)
        self.assertEqual(tool.release(self.j, post, ID)['status'], 'REFUSED')
        self.assertEqual(calls, [])  # refused before any server read
        self.assertEqual(self.dump(), before)
        self.assertEqual(tool.release(self.j, post, 'demo-missing')['status'], 'REFUSED')

    def test_journal_release_requires_the_exact_stored_effect(self):
        for bad in ('', None, '{"different":"body"}'):
            with self.subTest(expected=bad), self.assertRaises(sc.CustodyUnknown):
                self.j.release_unconfirmed(ID, bad)
        self.assertEqual(self.j.next()[2], self.body)
        self.assertEqual(self.j.release_unconfirmed(ID, self.body), 10)
        with self.assertRaises(sc.CustodyUnknown):  # nothing left to release
            self.j.release_unconfirmed(ID, self.body)

    def test_other_rows_are_never_touched(self):
        other = 'demo-other'
        self.j.retain(page(11, subject=nw.ITEM + other))
        self.j.prepare(other, 11, {'other': 'body'})
        kept = self.j.db.execute('SELECT * FROM pending WHERE id=?', (other,)).fetchone()
        post, _ = server(ABSENT, ABSENT)
        self.assertEqual(tool.release(self.j, post, ID)['status'], 'RELEASED')
        self.assertEqual(self.j.db.execute('SELECT * FROM pending WHERE id=?', (other,)).fetchone(), kept)

    def test_prove_absent_rejects_unsafe_subjects(self):
        post, calls = server(ABSENT, ABSENT)
        with self.assertRaises(nw.NativeUnknown):
            sync.prove_absent(post, 'x> <y')
        self.assertEqual(calls, [])

    def test_cli_is_off_unless_enabled_and_refuses_without_custody(self):
        self.assertEqual(tool.main(['--state', str(self.path), '--item', ID]), 0)
        missing = Path(self.tmp.name) / 'absent.sqlite3'
        self.assertEqual(tool.main(['--enable', '--native-authority-stdin',
                                    '--state', str(missing), '--item', ID]), 2)


if __name__ == '__main__':
    unittest.main()
