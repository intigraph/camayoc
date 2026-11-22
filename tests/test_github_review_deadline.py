"""Real SQLite deadlines; synthetic authority and spawn, no live jobs or requests.

Quipu request budget per exact target: arm <=1 resolver read; cancel 0;
fire <=1 resolver +1 native custody read. Local verification and SQL use 0.
No periodic scan, retry, forged webhook row or HTTP is used by these tests.
"""
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from github_recruitment import Candidate, attempt
from github_review_deadline import Deadlines, Readiness, Wake


class DeadlineTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:', isolation_level=None)
        self.addCleanup(self.conn.close)
        self.store = Deadlines(self.conn, repos=frozenset({'intigraph/widget'}))
        self.now = 100.
        self.candidate = Candidate('intigraph/widget', 1, 'a' * 40, 'review', 'task-1')
        self.wake = Wake('intigraph/widget', 1, 'a' * 40, 'green-1', 'green')
        self.reads, self.jobs = [], []

    def ready(self, *target):
        self.reads.append(('resolve', target))
        return Readiness(self.candidate, 100., self.now, True, True)

    def admit(self, candidate):
        self.reads.append(('custody', candidate))

    def arm(self, wake=None, **changes):
        options = dict(verify_event=lambda _: None, resolve_ready=self.ready, clock=lambda: self.now)
        options.update(changes)
        return self.store.on_event(wake or self.wake, **options)

    def fire(self, **changes):
        options = dict(resolve_ready=self.ready, admit=self.admit,
                       spawn=lambda c: self.jobs.append(c) or 'fixture-receipt', clock=lambda: self.now)
        options.update(changes)
        return self.store.fire(self.wake.repo, self.wake.pr, self.wake.head, **options)

    def test_exact_two_hours_once_with_explicit_request_budget(self):
        self.assertEqual({'state': 'armed', 'due_at': 7300.}, self.arm())
        self.assertEqual(1, len(self.reads))  # <=1 Quipu read on green event.
        self.now = 7299.
        self.assertEqual('not-due', self.fire())
        self.assertEqual(1, len(self.reads))  # Early scheduler callback reads no authority.
        self.now = 7300.
        self.assertEqual('spawned', self.fire())
        self.assertEqual(['resolve', 'resolve', 'custody'], [x[0] for x in self.reads])
        self.assertEqual('duplicate', self.fire())
        self.assertEqual(1, len(self.jobs))

    def test_review_or_push_cancels_without_reads_and_cannot_rearm(self):
        self.arm()
        self.assertEqual('cancelled', self.arm(replace(self.wake, delivery='review-1', kind='review'))['state'])
        self.assertEqual(1, len(self.reads))  # 0 Quipu reads on cancellation.
        self.now = 7300.
        self.assertEqual('duplicate', self.fire())
        self.assertEqual('cancelled', self.arm(replace(self.wake, delivery='green-2'))['state'])
        self.assertFalse(self.jobs)

    def test_actual_fixture_http_request_budget(self):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(server):
                server.rfile.read(int(server.headers['Content-Length']))
                requests.append(server.path)
                server.send_response(200)
                server.end_headers()
                server.wfile.write(b'{}')

            def log_message(server, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        thread.start()
        def read():
            with urlopen(Request('http://127.0.0.1:' + str(server.server_port) + '/query',
                                 data=b'{}', headers={'Content-Type': 'application/json'}), timeout=1) as response:
                response.read()
        def resolve(*target):
            read()
            return self.ready(*target)
        def admit(candidate):
            read()
        try:
            self.arm(resolve_ready=resolve)
            self.assertEqual(1, len(requests))  # Arm <=1 actual fixture Quipu request.
            self.now = 7300.
            self.assertEqual('spawned', self.fire(resolve_ready=resolve, admit=admit))
            self.assertEqual(3, len(requests))  # Fire <=2; no scans or repeated reads.
            self.arm(replace(self.wake, delivery='budget-review', kind='review'), resolve_ready=resolve)
            self.assertEqual(3, len(requests))  # Cancellation 0, including HTTP.
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)

    def test_same_head_push_preserves_deadline_and_cancellation_fences(self):
        self.arm()
        self.arm(replace(self.wake, delivery='late-same-head-push', kind='push'))
        self.assertEqual(1, len(self.reads))  # Cancellation wake has no authority IO.
        self.now = 7300.
        def same_head_during_admission(candidate):
            self.arm(replace(self.wake, delivery='same-head-during-admit', kind='push'))
            return self.admit(candidate)
        self.assertEqual('spawned', self.fire(admit=same_head_during_admission))
        self.assertEqual('duplicate', self.fire())
        self.assertEqual(1, len(self.jobs))

    def test_push_to_new_head_cancels_old_deadline(self):
        self.arm()
        self.arm(replace(self.wake, delivery='push-1', kind='push', head='b' * 40))
        self.now = 7300.
        self.assertEqual('duplicate', self.fire())
        self.assertEqual('cancelled', self.arm(replace(self.wake, delivery='old-green'))['state'])

    def test_cancel_during_resolver_prevents_late_arm(self):
        def racing(*target):
            self.arm(replace(self.wake, delivery='race-review', kind='review'))
            return self.ready(*target)
        self.assertEqual('cancelled', self.arm(resolve_ready=racing)['state'])
        self.now = 7300.
        self.assertEqual('duplicate', self.fire())

    def test_old_head_review_does_not_cancel_current_head(self):
        self.arm()
        self.arm(replace(self.wake, delivery='old-review', kind='review', head='b' * 40))
        self.now = 7300.
        self.assertEqual('spawned', self.fire())

    def test_cancel_after_reservation_prevents_transport(self):
        self.arm()
        self.now = 7300.
        def racing(c):
            self.arm(replace(self.wake, delivery='race-review', kind='review'))
        self.assertEqual('refused', self.fire(admit=racing))
        self.assertFalse(self.jobs)

    def test_current_head_verdict_checks_and_duration_are_rechecked(self):
        self.arm()
        self.now = 7300.
        for value in [None, replace(self.ready(), unreviewed=False),
                      replace(self.ready(), complete_green=False),
                      replace(self.ready(), green_since=200.),
                      replace(self.ready(), observed_at=1.),
                      replace(self.ready(), candidate=replace(self.candidate, head='b' * 40)),
                      replace(self.ready(), candidate=replace(self.candidate, item='other-item'))]:
            with self.subTest(value=value):
                # Separate persisted connection: refused keys may never reset.
                conn = sqlite3.connect(':memory:', isolation_level=None)
                self.addCleanup(conn.close)
                store = Deadlines(conn, repos=self.store.repos)
                self.now = 100.
                store.on_event(self.wake, verify_event=lambda _: None, resolve_ready=self.ready, clock=lambda: self.now)
                self.now = 7300.
                self.assertEqual('refused', store.fire(self.wake.repo, 1, self.wake.head,
                    resolve_ready=lambda *a: value, admit=self.admit,
                    spawn=lambda c: self.jobs.append(c) or 'receipt', clock=lambda: self.now))
        self.assertFalse(self.jobs)

    def test_lost_reply_and_interrupted_firing_remain_terminal(self):
        self.arm()
        self.now = 7300.
        def lost(c):
            self.jobs.append(c)
            raise TimeoutError('private transport output')
        self.assertEqual('unknown', self.fire(spawn=lost))
        self.assertEqual('duplicate', self.fire())
        self.assertEqual(1, len(self.jobs))
        self.assertNotIn('private transport', str(self.conn.execute('SELECT * FROM recruitment_attempts').fetchall()))

    def test_webhook_and_deadline_share_candidate_key(self):
        self.arm()
        self.now = 7300.
        self.assertEqual('spawned', attempt(self.store.attempts, self.candidate,
            delivery='verified-webhook', event_at=self.now, admit=self.admit,
            spawn=lambda c: self.jobs.append(c) or 'receipt', clock=lambda: self.now))
        self.assertEqual('duplicate', self.fire())
        self.assertEqual(1, len(self.jobs))

    def test_restart_retains_deadline_and_crash_tombstone(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'deadlines.db'
            first = sqlite3.connect(path, isolation_level=None)
            store = Deadlines(first, repos=self.store.repos)
            store.on_event(self.wake, verify_event=lambda _: None, resolve_ready=self.ready, clock=lambda: self.now)
            first.execute("UPDATE review_deadlines SET state='firing'")
            first.close()
            second = sqlite3.connect(path, isolation_level=None)
            self.addCleanup(second.close)
            store = Deadlines(second, repos=self.store.repos)
            self.now = 7300.
            self.assertEqual('duplicate', store.fire(self.wake.repo, 1, self.wake.head,
                resolve_ready=self.ready, admit=self.admit, spawn=lambda c: 'bad', clock=lambda: self.now))

    def test_second_connection_observes_claim_before_transport(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'deadlines.db'
            first = sqlite3.connect(path, isolation_level=None)
            second = sqlite3.connect(path, isolation_level=None)
            self.addCleanup(first.close)
            self.addCleanup(second.close)
            one, two = [Deadlines(c, repos=self.store.repos) for c in (first, second)]
            one.on_event(self.wake, verify_event=lambda _: None, resolve_ready=self.ready, clock=lambda: self.now)
            self.now = 7300.
            def transport(c):
                self.assertEqual('attempted', second.execute('SELECT state FROM recruitment_attempts').fetchone()[0])
                self.assertEqual('duplicate', two.fire(c.repo, c.pr, c.head,
                    resolve_ready=self.ready, admit=self.admit, spawn=lambda c: 'bad', clock=lambda: self.now))
                self.jobs.append(c)
                return 'receipt'
            self.assertEqual('spawned', one.fire(self.wake.repo, 1, self.wake.head,
                resolve_ready=self.ready, admit=self.admit, spawn=transport, clock=lambda: self.now))
            self.assertEqual(1, len(self.jobs))

    def test_authentication_foreign_and_unknown_never_spend_candidate_key(self):
        self.assertEqual('refused', self.arm(verify_event=lambda _: 'denied')['state'])
        self.assertEqual('foreign', self.arm(replace(self.wake, repo='intigraph/foreign'))['state'])
        self.assertEqual('ignored', self.arm(resolve_ready=lambda *a: None)['state'])
        self.assertEqual('duplicate', self.arm()['state'])
        self.assertFalse(self.reads)
        self.assertEqual(0, self.conn.execute('SELECT count(*) FROM recruitment_attempts').fetchone()[0])

    def test_authenticator_exception_is_unknown_without_leaking_text(self):
        def unavailable(wake):
            raise OSError('private verifier path or secret')
        self.assertEqual({'state': 'unknown'}, self.arm(verify_event=unavailable))
        self.assertFalse(self.reads)
        self.assertEqual(0, self.conn.execute('SELECT count(*) FROM review_deadline_events').fetchone()[0])

    def test_merge_and_untrusted_time_never_arm(self):
        for value in [replace(self.ready(), candidate=replace(self.candidate, job='merge')),
                      replace(self.ready(), green_since=float('nan')),
                      replace(self.ready(), green_since=True),
                      replace(self.ready(), observed_at=101.),
                      replace(self.ready(), complete_green=1)]:
            self.assertEqual('ignored', self.arm(replace(self.wake, delivery='case-' + str(len(self.reads))),
                resolve_ready=lambda *a: value)['state'])
            self.reads.append(('case', None))
        self.assertEqual(0, self.conn.execute('SELECT count(*) FROM review_deadlines').fetchone()[0])


if __name__ == '__main__':
    unittest.main()
