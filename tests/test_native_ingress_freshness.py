"""aegis-ksd: native WorkItem ingress publishes SUCCESS freshness apart from attempts.

wu's 2026-10-15 ruling: freshness is measured from the last successful verified
receipt; a failed tick must carry the previous success forward, never report a
new one, and graph coverage is disclosed rather than implied.
"""
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import seed_change_custody
import seed_work_item_sync
import seed_work_items
import sync_work_items as sync


class FakeJournal:
    def __init__(self, *args, **kwargs):
        self.db = SimpleNamespace(close=lambda: None)


class NativeFreshness(unittest.TestCase):
    def run_tick(self, state, now, receipt):
        argv = ['sync', '--native-authority-stdin', '--state', str(state), '--actor', 'tracker-workitems',
                '--source', 'seeds:aegis', '--publish-status']
        reader = SimpleNamespace(board=SimpleNamespace(graph='https://seeds.local/project/aegis'))
        stdin = SimpleNamespace(buffer=io.BytesIO(b'{}'))
        with patch.object(sys, 'argv', argv), patch.object(sys, 'stdin', stdin), \
                patch.object(sync.time, 'time', return_value=now), \
                patch.object(seed_work_items, 'from_authority', return_value=reader), \
                patch.object(seed_change_custody, 'Journal', FakeJournal), \
                patch.object(seed_work_item_sync, 'tick', return_value=dict(receipt)), \
                patch.object(sync.camayoc_metrics, 'push', return_value=(True, '')) as push, \
                redirect_stdout(io.StringIO()):
            code = sync.main()
        return code, push.call_args.args[1]

    def test_success_freshness_is_carried_not_invented(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'seeds-state.sqlite3'
            failed = {'status': 'UNKNOWN', 'requests': 1, 'writes': 0, 'verified': 0, 'cursor': 41, 'backlog': 0}
            ok = {'status': 'OK', 'requests': 3, 'writes': 1, 'verified': 1, 'cursor': 42, 'backlog': 0}
            # Before any success: no success timestamp at all (absent, not 0, not now).
            code, text = self.run_tick(state, 1000.0, failed)
            self.assertEqual(code, 2)
            self.assertNotIn('camayoc_workitem_ingress_last_success_timestamp_seconds', text)
            self.assertIn('camayoc_workitem_ingress_timestamp_seconds 1000', text)
            # A success sets it to that tick's start.
            code, text = self.run_tick(state, 1100.0, ok)
            self.assertEqual(code, 0)
            self.assertIn('camayoc_workitem_ingress_last_success_timestamp_seconds 1100', text)
            self.assertIn('camayoc_workitem_ingress_cursor 42', text)
            self.assertIn('camayoc_workitem_ingress_requests 3', text)
            self.assertIn('coverage="board-graph-only"', text)
            self.assertIn('graph="https://seeds.local/project/aegis"', text)
            # A later failure carries the PREVIOUS success forward and moves only the attempt.
            code, text = self.run_tick(state, 1200.0, failed)
            self.assertEqual(code, 2)
            self.assertIn('camayoc_workitem_ingress_last_success_timestamp_seconds 1100', text)
            self.assertIn('camayoc_workitem_ingress_timestamp_seconds 1200', text)


if __name__ == '__main__':
    unittest.main()
