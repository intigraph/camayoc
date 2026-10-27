import io
import json
import queue
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

import seed_change_custody as custody
import seed_change_stream
import seed_work_item_stream as driver
from test_seed_change_custody import page
from test_seed_change_stream import fill_to_capacity, subject, wide_transaction
from test_seed_work_item_sync import Tick


class PersistentDriver(Tick):
    def setUp(self):
        super().setUp()
        self.j.db.close()
        self.path = Path(self.tmp.name) / 'native.sqlite3'
        self.j = custody.Journal(self.path, self.reader.board,
                                server='https://peer-one.example.test', initial_tx=9)
        self.j.retain(page())
        self.j.bind_stream_source({'store_id': 'fixture', 'log_epoch': 'one'}, self.j.cursor)
        self.identity = Path(self.tmp.name) / 'identity.json'
        self.identity.write_text(json.dumps({'store_id': 'fixture', 'log_epoch': 'one'}))

    def run_driver(self, verified):
        receipts, subscriptions = [], []
        now = [1000]
        class Subscription:
            connected = True
            source_verified = verified
            errors = 0
            paused = False
            closed = False
            turns = 0
            def __init__(self, *args):
                subscriptions.append(self)
            def retain_one(self, journal):
                self.turns += 1
                if self.turns == 4:
                    raise KeyboardInterrupt
                now[0] = [1000, 1001, 1061][self.turns - 1]
                return False
            def close(self):
                self.closed = True
        args = SimpleNamespace(state=self.path, stream_identity=self.identity, initial_tx=None,
                               actor='fixture', source='urn:fixture', publish_status=False)
        with (patch.object(driver.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(b'{}'))),
              patch.object(driver.native, 'from_authority', return_value=self.reader),
              patch.object(driver.planes, 'SERVER', 'https://peer-one.example.test'),
              patch.object(driver.planes, 'auth_token', return_value=None),
              patch.object(driver.planes, '_post',
                           side_effect=lambda endpoint, body, **kwargs: self.post(endpoint, body)),
              patch.object(driver.planes, 'plane_for', return_value='urn:private:knowledge'),
              patch.object(driver.seed_change_stream, 'Subscription', Subscription),
              patch.object(driver.time, 'time', side_effect=lambda: now[0]),
              patch.object(driver.driver, 'publish_native_receipt',
                           side_effect=lambda args, receipt, *rest: receipts.append(dict(receipt)) or 0)):
            with self.assertRaises(KeyboardInterrupt):
                driver.run(args)
        self.assertEqual(len(subscriptions), 1)
        self.assertTrue(subscriptions[0].closed)
        return receipts

    def test_tick_cadence_is_not_increased_by_stream_wakeups(self):
        receipts = self.run_driver(True)
        self.assertEqual(len(receipts), 2)  # 1001 cannot create another effect tick.
        self.assertEqual(receipts[0]['verified'], 1, receipts)
        self.assertEqual(receipts[0]['requests'], 4)
        self.assertEqual(receipts[1]['requests'], 0)
        self.assertEqual(self.writes, 1)

    def test_unverified_connection_never_drains_pending_effects(self):
        receipts = self.run_driver(False)
        self.assertEqual(len(receipts), 2)
        self.assertTrue(all(r['status'] == 'UNKNOWN' and r['requests'] == 0 for r in receipts))
        self.assertEqual(self.writes, 0)
        self.assertIsNotNone(self.j.next())
        saved = json.loads(self.path.with_suffix('.receipt.json').read_text())
        self.assertIsNone(saved['last_success_at'])

    def test_missing_custody_is_not_initialized(self):
        missing = Path(self.tmp.name) / 'missing.sqlite3'
        with self.assertRaises(custody.CustodyUnknown):
            driver.run(SimpleNamespace(state=missing, initial_tx=None))
        self.assertFalse(missing.exists())

    def test_missing_source_binding_refuses_before_opening_transport(self):
        self.j.db.execute('DROP TABLE stream_source')
        with self.assertRaises(custody.CustodyUnknown):
            self.run_driver(True)
        self.assertEqual(self.writes, 0)
        self.assertEqual(self.j.cursor, 10)

    def test_source_binding_cannot_be_replaced_or_created_at_a_stale_cursor(self):
        changed = {'store_id': 'replacement', 'log_epoch': 'new'}
        before = self.j.db.execute('SELECT * FROM pending').fetchall()
        with self.assertRaises(custody.CustodyUnknown):
            self.j.bind_stream_source(changed, self.j.cursor)
        with self.assertRaises(custody.CustodyUnknown):
            self.j.bind_stream_source({'store_id': 'fixture', 'log_epoch': 'one'}, 9)
        self.j.verify_stream_source({'store_id': 'fixture', 'log_epoch': 'one'})
        self.assertEqual(self.j.db.execute('SELECT * FROM pending').fetchall(), before)
        self.assertEqual(self.j.cursor, 10)

    def test_explicit_transition_cli_binds_once_without_touching_pending_or_cursor(self):
        self.j.db.execute('DROP TABLE stream_source')
        before = self.j.db.execute('SELECT * FROM pending').fetchall()
        argv = ['sync_work_items.py', '--native-authority-stdin', '--state', str(self.path),
                '--actor', 'fixture', '--source', 'urn:fixture', '--bind-stream-source',
                '--stream-identity', str(self.identity), '--expected-stream-cursor', '10']
        with (patch.object(driver.sys, 'argv', argv),
              patch.object(driver.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(b'{}'))),
              patch.object(driver.native, 'from_authority', return_value=self.reader),
              patch.object(driver.planes, 'SERVER', 'https://peer-one.example.test')):
            self.assertEqual(driver.driver.main(), 0)
        self.j.verify_stream_source({'store_id': 'fixture', 'log_epoch': 'one'})
        self.assertEqual(self.j.cursor, 10)
        self.assertEqual(self.j.db.execute('SELECT * FROM pending').fetchall(), before)

    def test_changed_identity_file_cannot_adopt_prior_custody(self):
        self.identity.write_text(json.dumps({'store_id': 'replacement', 'log_epoch': 'new'}))
        with self.assertRaises(custody.CustodyUnknown):
            self.run_driver(True)
        self.assertEqual(self.writes, 0)
        self.assertEqual(self.j.cursor, 10)


class FullCustodyDriver(PersistentDriver):
    """Review P2: the real driver loop and real retain_one at full custody."""

    def run_real(self, times, verified=True):
        """Drive run() with the real Subscription.retain_one; one turn per time."""
        offered, receipts = [], []
        now = [times[0]]
        test = self
        class Delivering(seed_change_stream.Subscription):
            def __init__(self, server, graph, cursor, identity, headers):
                # No thread: the fixture plays the transport. A restarted
                # subscription is redelivered the first unretained transaction.
                self.cursor, self.connected, self.errors = cursor, True, 0
                self.source_verified = verified
                self.pending = queue.Queue(maxsize=1)
                self.stop = threading.Event()
                self.turns, self.closed = 0, False
                self.done = threading.Event()
                self.pending.put((cursor + 1, wide_transaction(cursor + 1, [subject('-next')]), self.done))
                offered.append(self)
            def retain_one(self, journal, timeout=1):
                if self.turns == len(times):
                    raise KeyboardInterrupt
                now[0] = times[self.turns]
                self.turns += 1
                return super().retain_one(journal, timeout)
            def close(self):
                self.closed = True
                super().close()
        args = SimpleNamespace(state=self.path, stream_identity=self.identity, initial_tx=None,
                               actor='fixture', source='urn:fixture', publish_status=False)
        with (patch.object(driver.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(b'{}'))),
              patch.object(driver.native, 'from_authority', return_value=self.reader),
              patch.object(driver.planes, 'SERVER', 'https://peer-one.example.test'),
              patch.object(driver.planes, 'auth_token', return_value=None),
              patch.object(driver.planes, '_post',
                           side_effect=lambda endpoint, body, **kwargs: test.post(endpoint, body)),
              patch.object(driver.planes, 'plane_for', return_value='urn:private:knowledge'),
              patch.object(driver.seed_change_stream, 'Subscription', Delivering),
              patch.object(driver.time, 'time', side_effect=lambda: now[0]),
              patch.object(seed_change_stream.time, 'sleep', lambda seconds: None),
              patch.object(driver.driver, 'publish_native_receipt',
                           side_effect=lambda args, receipt, *rest: receipts.append(dict(receipt)) or 0)):
            with self.assertRaises(KeyboardInterrupt):  # Only the fixture ends the loop.
                driver.run(args)
        self.assertEqual(len(offered), 1)
        self.assertTrue(offered[0].closed)
        return offered[0], receipts

    def ids(self):
        return [row[0] for row in self.j.db.execute('SELECT id FROM pending')]

    def test_full_custody_pauses_intake_drains_and_resumes_without_exiting(self):
        fill_to_capacity(self.j)
        sub, receipts = self.run_real([1000, 1001, 1002])
        # Turn 1 holds tx 11 and still reaches the effect tick, which drains.
        self.assertEqual(receipts[0]['stream_admission_paused'], 1, receipts)
        self.assertEqual(receipts[0]['verified'], 1, receipts)
        self.assertEqual(self.writes, 1)
        # Turn 2 admits the held transaction once; the cursor moves with it.
        self.assertTrue(sub.done.is_set())
        self.assertFalse(sub.paused)
        self.assertEqual(self.j.cursor, 11)
        ids = self.ids()
        self.assertEqual(ids.count('demo-fill-next'), 1)
        self.assertNotIn('demo-one', ids)
        self.assertEqual(len(ids), custody.MAX_PENDING)
        self.assertEqual(len(receipts), 1)  # Minute effect budget unchanged.

    def test_restart_while_paused_keeps_cursor_and_recovers_the_same_transaction(self):
        fill_to_capacity(self.j)
        # First process: unverified source, so no drain; it stops while held.
        sub, receipts = self.run_real([1000, 1001], verified=False)
        self.assertTrue(sub.paused)
        self.assertFalse(sub.done.is_set())  # Never acknowledged to transport.
        self.assertEqual(self.j.cursor, 10)
        self.assertEqual(len(self.ids()), custody.MAX_PENDING)
        self.assertNotIn('demo-fill-next', self.ids())
        self.assertEqual(self.writes, 0)
        # Restart: the transport redelivers tx 11 from the retained cursor.
        sub, receipts = self.run_real([2000, 2001, 2002])
        self.assertEqual(receipts[0]['verified'], 1, receipts)
        self.assertTrue(sub.done.is_set())
        self.assertEqual(self.j.cursor, 11)
        self.assertEqual(self.ids().count('demo-fill-next'), 1)
        self.assertNotIn('demo-one', self.ids())
        self.assertEqual(self.writes, 1)
