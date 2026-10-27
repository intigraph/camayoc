import io
import hashlib
import json
import queue
import sys
import threading
import unittest
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

import change_frames
import seed_change_custody as custody
import seed_change_stream as stream
import seed_work_item_sync as sync
from test_seed_change_custody import page
from test_seed_work_item_sync import Tick
from test_seed_work_items import GRAPH
import seed_work_items as nw

IDENTITY = {'store_id': 'fixture-store', 'log_epoch': 'fixture-epoch'}


def transaction():
    return {**page(), 'capture': 'old_and_new_values', 'source': dict(IDENTITY)}


class StreamCustody(Tick):
    def owner(self, body=None):
        sub = object.__new__(stream.Subscription)
        sub.cursor = self.j.cursor
        sub.pending = queue.Queue(maxsize=1)
        done = threading.Event()
        sub.pending.put((10, body or transaction(), done))
        return sub, done

    def test_complete_stream_retains_candidates_before_ack_without_poll(self):
        sub, done = self.owner()
        self.assertTrue(sub.retain_one(self.j, timeout=0))
        self.assertTrue(done.is_set())
        self.assertEqual(sub.cursor, 10)
        self.assertEqual(self.j.cursor, 10)
        self.assertIsNotNone(self.j.next())
        result = sync.tick(self.reader, self.j, self.post, changes=None,
                           plane='urn:private:knowledge', actor='fixture')
        self.assertEqual((result['status'], result['requests'], result['writes']), ('OK', 4, 1))
        self.assertIsNone(self.j.next())

    def test_refused_local_commit_keeps_both_cursors(self):
        sub, done = self.owner()
        with patch.object(self.j, 'retain', side_effect=RuntimeError('sqlite refused')):
            with self.assertRaises(RuntimeError):
                sub.retain_one(self.j, timeout=0)
        self.assertTrue(done.is_set())
        self.assertEqual((sub.cursor, self.j.cursor), (9, 9))
        self.assertIsNone(self.j.next())

    def test_foreign_graph_never_advances(self):
        body = transaction()
        body['records'][0]['graph'] = 'urn:other'
        sub, done = self.owner(body)
        with self.assertRaises(custody.CustodyUnknown):
            sub.retain_one(self.j, timeout=0)
        self.assertEqual((sub.cursor, self.j.cursor), (9, 9))
        self.assertTrue(done.is_set())

    def test_idle_tick_does_no_feed_request_or_write(self):
        result = sync.tick(self.reader, self.j, self.post, changes=None,
                           plane='urn:private:knowledge', actor='fixture')
        self.assertEqual((result['status'], result['requests'], result['writes']), ('OK', 0, 0))

    def test_pending_lost_effect_is_verified_not_resent_on_stream_ticks(self):
        sub, _ = self.owner()
        sub.retain_one(self.j, timeout=0)
        self.drop = True
        result = sync.tick(self.reader, self.j, self.post, changes=None,
                           plane='urn:private:knowledge', actor='fixture')
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertEqual(self.writes, 1)
        self.drop = False
        result = sync.tick(self.reader, self.j, self.post, changes=None,
                           plane='urn:private:knowledge', actor='fixture')
        self.assertEqual(result['verified'], 1)
        self.assertEqual(self.writes, 1)

    def test_real_subscription_uses_stream_and_acknowledges_local_commit(self):
        requests, release = [], threading.Event()
        body = json.dumps(transaction())
        wire = f'event: quipu.changes\nid: 10\ndata: {body}\n\n'.encode()
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append((self.path, dict(self.headers)))
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                self.wfile.write(wire)
                self.wfile.flush()
                release.wait(5)
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        sub = stream.Subscription(f'http://127.0.0.1:{server.server_port}', self.reader.board.graph,
                                  self.j.cursor, IDENTITY, {'X-Quipu-Client': 'camayoc-ingress'})
        try:
            self.assertTrue(sub.retain_one(self.j, timeout=3))
            self.assertEqual((sub.cursor, self.j.cursor), (10, 10))
            self.assertTrue(sub.source_verified)
            self.assertEqual(len(requests), 1)
            self.assertTrue(requests[0][0].startswith('/changes/stream?'))
            self.assertIn('graph=', requests[0][0])
            self.assertEqual(requests[0][1]['Last-Event-Id'], '9')
        finally:
            sub.close()
            release.set()
            sub.thread.join(3)
            server.shutdown()
            server.server_close()
            worker.join(3)
        self.assertFalse(sub.thread.is_alive())


class WireAdmission(unittest.TestCase):
    def test_decoder_matches_pinned_canonical_origin(self):
        root = Path(__file__).resolve().parents[1]
        origin = json.loads((root / 'docs/protocol/change-frames-origin.json').read_text())
        self.assertEqual(origin['repository'], 'https://github.com/intigraph/chaski')
        self.assertEqual(len(origin['commit']), 40)
        self.assertEqual(hashlib.sha256((root / 'scripts/change_frames.py').read_bytes()).hexdigest(),
                         origin['sha256'])

    def test_exact_identity_and_capture_are_required(self):
        for mutation in ('epoch', 'store', 'new_row', 'new_values', 'missing_source'):
            with self.subTest(mutation=mutation):
                body = transaction()
                if mutation in ('epoch', 'store'):
                    body['source']['log_epoch' if mutation == 'epoch' else 'store_id'] = 'changed'
                elif mutation == 'missing_source':
                    del body['source']
                else:
                    body['capture'] = mutation
                with self.assertRaises(custody.CustodyUnknown):
                    stream.admitted_page(body, IDENTITY)
        self.assertEqual(stream.admitted_page(transaction(), IDENTITY), transaction())

    def test_identity_or_cursor_unknown_never_opens_transport(self):
        with patch.object(threading.Thread, 'start', side_effect=AssertionError('opened')):
            for identity, cursor in (({}, 9), (IDENTITY, True), (IDENTITY, -1)):
                with self.assertRaises(custody.CustodyUnknown):
                    stream.Subscription('https://peer.invalid', 'urn:board', cursor, identity, {})

    def test_native_budget_remains_bounded_and_whole(self):
        body = transaction()
        body['records'][0]['value'] = {'text': 'x' * custody.MAX_BYTES}
        with self.assertRaises(custody.CustodyUnknown):
            stream.admitted_page(body, IDENTITY)
        body = transaction()
        body['records'] *= custody.MAX_RECORDS + 1
        with self.assertRaises(custody.CustodyUnknown):
            stream.admitted_page(body, IDENTITY)

    def test_truncated_wire_never_yields_partial_transaction(self):
        body = json.dumps(transaction())
        wire = f'event: quipu.changes\nid: 10\ndata: {body}\n'.encode()
        with self.assertRaises(change_frames.ProtocolError):
            list(change_frames.frames(io.BytesIO(wire)))

    def test_stream_capture_preserves_old_edge_parent_candidates(self):
        body = transaction()
        body['records'][0].update(attribute='https://schema.org/parentItem',
                                  old_value={'ref': self._subject('demo-prior')})
        # No conversion to a lossy semantic event or invented new_row occurs.
        self.assertIn('old_value', stream.admitted_page(body, IDENTITY)['records'][0])

    @staticmethod
    def _subject(item):
        return 'https://seeds.local/item/' + item


def subject(n):
    return nw.ITEM + f'demo-fill{n}'


def wide_transaction(tx, subjects):
    records = [{'graph': GRAPH, 'tx': tx, 'sequence': i + 1, 'entity': s,
                'attribute': nw.SCHEMA + 'name', 'op': 'assert'}
               for i, s in enumerate(subjects)]
    return {'records': records, 'next_tx': tx, 'watermark_tx': tx,
            'capture': 'old_and_new_values', 'source': dict(IDENTITY)}


def fill_to_capacity(journal):
    """Pad custody to exactly MAX_PENDING behind the real drainable item.

    Filler rows carry a far-future attempt, so the real fixture item is
    drained first by the unchanged effect tick.
    """
    have = journal.db.execute('SELECT COUNT(*) FROM pending').fetchone()[0]
    journal.db.executemany('INSERT INTO pending(id,tx,last_attempt) VALUES(?,?,1e18)',
                           [(f'demo-fill{n}', 10) for n in range(custody.MAX_PENDING - have)])
    journal.db.commit()


class FullCustodyBackpressure(Tick):
    """Review P2: full custody pauses admission; it never exits the consumer."""

    def offer(self, sub, stream_id, body):
        done = threading.Event()
        sub.pending.put((stream_id, body, done))
        return done

    def subscription(self):
        sub = object.__new__(stream.Subscription)
        sub.cursor = self.j.cursor
        sub.pending = queue.Queue(maxsize=1)
        return sub

    def pending_ids(self):
        return [row[0] for row in self.j.db.execute('SELECT id FROM pending')]

    def test_full_custody_holds_transaction_drains_then_admits_once(self):
        self.j.retain(page())
        fill_to_capacity(self.j)
        sub = self.subscription()
        nxt = wide_transaction(11, [subject('-next')])
        done = self.offer(sub, 11, nxt)
        for _ in range(2):  # Repeated turns keep holding; nothing is skipped.
            self.assertFalse(sub.retain_one(self.j, timeout=0))
            self.assertTrue(sub.paused)
            self.assertFalse(done.is_set())  # Transport not acknowledged: intake paused.
            self.assertEqual((sub.cursor, self.j.cursor), (10, 10))
            self.assertEqual(len(self.pending_ids()), custody.MAX_PENDING)
            self.assertNotIn('demo-fill-next', self.pending_ids())
        drained = sync.tick(self.reader, self.j, self.post, changes=None,
                            plane='urn:private:knowledge', actor='fixture')
        # The drain tick still runs and verifies the real item. (Filler rows
        # are not resolvable by this fixture, so the tick stops after it.)
        self.assertEqual((drained['verified'], drained['writes'], drained['backlog']),
                         (1, 1, custody.MAX_PENDING - 1), drained)
        self.assertNotIn('demo-one', self.pending_ids())
        self.assertTrue(sub.retain_one(self.j, timeout=0))
        self.assertFalse(sub.paused)
        self.assertTrue(done.is_set())
        self.assertEqual((sub.cursor, self.j.cursor), (11, 11))
        ids = self.pending_ids()
        self.assertEqual(ids.count('demo-fill-next'), 1)
        self.assertEqual(len(ids), custody.MAX_PENDING)
        # Admitted exactly once: an empty queue admits nothing further.
        self.assertFalse(sub.retain_one(self.j, timeout=0))
        self.assertEqual(self.j.cursor, 11)

    def test_held_transaction_is_the_next_one_admitted_in_order(self):
        self.j.retain(page())
        fill_to_capacity(self.j)
        sub = self.subscription()
        done11 = self.offer(sub, 11, wide_transaction(11, [subject('-a')]))
        self.assertFalse(sub.retain_one(self.j, timeout=0))
        # The reader is blocked on done11, but even a queued later transaction
        # must not overtake the held one.
        done12 = self.offer(sub, 12, wide_transaction(12, [subject('-b')]))
        self.j.db.execute("DELETE FROM pending WHERE id='demo-fill0'")
        self.j.db.execute("DELETE FROM pending WHERE id='demo-fill1'")
        self.j.db.commit()
        self.assertTrue(sub.retain_one(self.j, timeout=0))
        self.assertTrue(done11.is_set())
        self.assertEqual(self.j.cursor, 11)
        self.assertFalse(done12.is_set())
        self.assertTrue(sub.retain_one(self.j, timeout=0))
        self.assertEqual(self.j.cursor, 12)
        self.assertIn('demo-fill-a', self.pending_ids())
        self.assertIn('demo-fill-b', self.pending_ids())

    def test_transaction_larger_than_all_custody_still_refuses(self):
        # Nothing to drain: waiting could never admit it, so it stays a refusal.
        sub = self.subscription()
        done = self.offer(sub, 10, wide_transaction(10, [subject(n) for n in range(custody.MAX_PENDING + 1)]))
        with self.assertRaises(custody.CustodyFull):
            sub.retain_one(self.j, timeout=0)
        self.assertTrue(done.is_set())
        self.assertFalse(sub.paused)
        self.assertEqual((sub.cursor, self.j.cursor), (9, 9))
        self.assertEqual(self.pending_ids(), [])

    def test_oversize_transaction_refuses_at_once_even_with_drainable_custody(self):
        # aegis-o113: with drainable items pending, an oversize transaction
        # used to pause until custody was empty and only then refuse.
        self.j.retain(page())
        before = self.pending_ids()
        sub = self.subscription()
        body = wide_transaction(11, [subject(n) for n in range(custody.MAX_PENDING + 1)])
        done = self.offer(sub, 11, body)
        with patch.object(self.j, 'retain', side_effect=AssertionError('wrote')):
            with self.assertRaises(custody.CustodyFull) as refused:
                sub.retain_one(self.j, timeout=0)
        self.assertEqual(str(refused.exception), 'pending custody is full; keep prior cursor')
        self.assertTrue(done.is_set())
        self.assertFalse(sub.paused)
        self.assertEqual(sub.paused_seconds, 0)
        self.assertEqual((sub.cursor, self.j.cursor), (10, 10))
        self.assertEqual(self.pending_ids(), before)

    def test_transaction_exactly_at_custody_size_is_admitted(self):
        sub = self.subscription()
        body = wide_transaction(10, [subject(n) for n in range(custody.MAX_PENDING)])
        done = self.offer(sub, 10, body)
        self.assertTrue(sub.retain_one(self.j, timeout=0))
        self.assertTrue(done.is_set())
        self.assertEqual(self.j.cursor, 10)
        self.assertEqual(len(self.pending_ids()), custody.MAX_PENDING)

    def test_paused_seconds_counts_from_first_hold_and_resets_on_resume(self):
        self.j.retain(page())
        fill_to_capacity(self.j)
        sub = self.subscription()
        self.offer(sub, 11, wide_transaction(11, [subject('-next')]))
        clock = [100.0]
        with (patch.object(stream.time, 'monotonic', side_effect=lambda: clock[0]),
              patch.object(stream.time, 'sleep', lambda seconds: None)):
            self.assertEqual(sub.paused_seconds, 0)
            self.assertFalse(sub.retain_one(self.j, timeout=0))
            clock[0] = 130.0
            self.assertFalse(sub.retain_one(self.j, timeout=0))  # Re-hold keeps the start.
            self.assertEqual(sub.paused_seconds, 30.0)
            self.j.db.execute("DELETE FROM pending WHERE id='demo-fill0'")
            self.j.db.commit()
            self.assertTrue(sub.retain_one(self.j, timeout=0))
            self.assertFalse(sub.paused)
            self.assertEqual(sub.paused_seconds, 0)
