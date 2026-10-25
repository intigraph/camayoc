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
