"""Producer timeout acceptance over real loopback HTTP and durable SQLite custody.

The peer deliberately loses one response. Its no-commit outcome is fixed before
that response is withheld; this is not proof that arbitrary remote writes have
stopped. Late-commit and prior-version controls live in the reconciliation suite.
"""
import json
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import test_unconfirmed_work_item_reconciliation as reconciliation


class HttpTimeout(unittest.TestCase):
    def setUp(self):
        self.model = reconciliation.Reconciliation(methodName='runTest')
        self.model.setUp()
        self.addCleanup(self.model.tearDown)
        self.route = self.model.post
        self.client_timed_out = threading.Event()
        self.writer_stopped = threading.Event()
        self.allow_late_commit = threading.Event()
        self.defer_commit = False
        self.handler_errors = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                try:
                    body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                    try:
                        if self.path == '/knot':
                            if body != owner.pending_envelope['request']:
                                raise AssertionError('HTTP body differs from durable custody')
                            owner.model.writes += 1
                            owner.model.last_envelope = owner.pending_envelope
                            lost = owner.model.timeout_once
                            owner.model.timeout_once = False
                            if not lost or owner.model.commit_before_timeout:
                                owner.model.description = owner.pending_envelope['description']
                                owner.model.commits = getattr(owner.model, 'commits', 0) + 1
                            if lost:
                                raise TimeoutError('withhold response')
                            result = {'replaced': True, 'tx_id': 99, 'conforms': True}
                        else:
                            result = owner.route(self.path, body)
                    except TimeoutError:
                        # The model has already selected commit or no commit.
                        # Keep the socket unanswered until the REAL client gives
                        # up. No sleep or inferred server lifetime is involved.
                        if owner.defer_commit:
                            if not owner.allow_late_commit.wait(5):
                                raise AssertionError('late commit was not released')
                            owner.model.description = owner.model.last_envelope['description']
                            owner.model.commits = getattr(owner.model, 'commits', 0) + 1
                        owner.writer_stopped.set()
                        if not owner.client_timed_out.wait(5):
                            raise AssertionError('client did not time out')
                        self.close_connection = True
                        return
                    payload = json.dumps(result).encode()
                    self.send_response(200)
                    self.send_header('Content-Length', str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                except Exception as error:
                    owner.handler_errors.append(type(error).__name__)
                    self.close_connection = True

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.model.post = self.post

    def stop_server(self):
        self.client_timed_out.set()
        self.allow_late_commit.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        self.assertFalse(self.thread.is_alive())
        self.assertEqual(self.handler_errors, [])

    def post(self, endpoint, body):
        if endpoint == '/knot':
            # Read producer custody on its owning thread. The remote peer only
            # receives the immutable request/envelope captured before send.
            self.pending_envelope = json.loads(self.model.j.next()[2])
        request = urllib.request.Request(
            'http://127.0.0.1:' + str(self.server.server_port) + endpoint,
            data=json.dumps(body).encode(),
            headers={'Content-Type': 'application/json'}, method='POST')
        # Only the deliberately lost response has a short deadline; ordinary
        # reads/writes have ample time and do not depend on scheduling speed.
        lost = endpoint == '/knot' and self.model.timeout_once
        try:
            with urllib.request.urlopen(request, timeout=0.2 if lost else 5) as response:
                return json.loads(response.read())
        except TimeoutError:
            if not lost:
                raise
            self.client_timed_out.set()
            if not self.defer_commit:
                self.assertTrue(self.writer_stopped.wait(5))
            raise TimeoutError('actual HTTP client deadline expired') from None

    def fail_write(self, *, committed):
        self.model.timeout_once = True
        self.model.commit_before_timeout = committed
        receipt = self.model.tick()
        self.assertEqual((receipt['status'], receipt['writes']), ('UNKNOWN', 1))
        self.assertTrue(self.client_timed_out.is_set())
        self.assertTrue(self.writer_stopped.is_set())
        self.assertIsNotNone(self.model.j.next()[2])

    def test_commit_then_client_timeout_verifies_without_duplicate(self):
        self.fail_write(committed=True)
        self.model.j.db.close()
        from test_seed_work_item_sync import sc
        self.model.j = sc.Journal(self.model.path, self.model.reader.board,
                                  server='https://peer-one.example.test')
        receipt = self.model.tick()
        self.assertEqual((receipt['status'], receipt['writes'], receipt['verified']),
                         ('OK', 0, 1))
        self.assertEqual((self.model.writes, self.model.commits), (1, 1))
        self.assertIsNone(self.model.j.next())

    def test_proven_no_commit_timeout_allows_one_fresh_write(self):
        self.fail_write(committed=False)
        self.assertEqual(getattr(self.model, 'commits', 0), 0)
        receipt = self.model.tick()
        self.assertEqual((receipt['status'], receipt['writes'], receipt['verified']),
                         ('OK', 0, 0))
        self.assertIsNone(self.model.j.next()[2])
        receipt = self.model.tick()
        self.assertEqual((receipt['status'], receipt['writes'], receipt['verified']),
                         ('OK', 1, 1))
        self.assertEqual((self.model.writes, self.model.commits), (2, 1))
        self.assertIsNone(self.model.j.next())

    def test_ambiguous_read_after_timeout_preserves_exact_custody(self):
        self.fail_write(committed=False)
        stored = self.model.j.next()[2]
        self.model.control_answer = {'result': False, 'truncated': True}
        for _ in range(3):
            receipt = self.model.tick()
            self.assertEqual((receipt['status'], receipt['writes'], receipt['verified']),
                             ('UNKNOWN', 0, 0))
            self.assertEqual(self.model.j.next()[2], stored)
        self.assertEqual(self.model.writes, 1)
        self.assertEqual(getattr(self.model, 'commits', 0), 0)

    def test_still_running_remote_write_cannot_be_released_by_absent_read(self):
        self.defer_commit = True
        self.model.timeout_once = True
        self.model.commit_before_timeout = False
        self.assertEqual(self.model.tick()['status'], 'UNKNOWN')
        self.assertTrue(self.client_timed_out.is_set())
        self.assertFalse(self.writer_stopped.is_set())
        stored = self.model.j.next()[2]
        self.assertIsNotNone(stored)
        # The HTTP client is gone but the original server operation is live.
        # Current absence is true and still cannot authorize a fresh writer.
        self.assertEqual(self.model.tick()['status'], 'UNKNOWN')
        self.assertEqual(self.model.j.next()[2], stored)
        self.assertEqual(self.model.writes, 1)
        self.allow_late_commit.set()
        self.assertTrue(self.writer_stopped.wait(5))
        receipt = self.model.tick()
        self.assertEqual((receipt['status'], receipt['writes'], receipt['verified']),
                         ('OK', 0, 1))
        self.assertEqual((self.model.writes, self.model.commits), (1, 1))
