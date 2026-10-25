import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

import seed_change_custody as custody
import seed_work_item_stream as driver
from test_seed_change_custody import page
from test_seed_work_item_sync import Tick


class PersistentDriver(Tick):
    def setUp(self):
        super().setUp()
        self.j.db.close()
        self.path = Path(self.tmp.name) / 'native.sqlite3'
        self.j = custody.Journal(self.path, self.reader.board,
                                server='https://peer-one.example.test', initial_tx=9)
        self.j.retain(page())
        self.identity = Path(self.tmp.name) / 'identity.json'
        self.identity.write_text(json.dumps({'store_id': 'fixture', 'log_epoch': 'one'}))

    def run_driver(self, verified):
        receipts, subscriptions = [], []
        now = [1000]
        class Subscription:
            connected = True
            source_verified = verified
            errors = 0
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
