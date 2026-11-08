"""aegis-ksd: native WorkItem ingress publishes SUCCESS freshness apart from attempts.

wu's 2026-10-15 ruling: freshness is measured from the last successful verified
receipt; a failed tick must carry the previous success forward, never report a
new one, and graph coverage is disclosed rather than implied.
"""
import io
import fcntl
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import seed_change_custody
import seed_work_item_sync
import seed_work_items
import sync_work_items as sync


class FakeJournal:
    def __init__(self, *args, **kwargs):
        self.db = SimpleNamespace(close=lambda: None)


class NativeFreshness(unittest.TestCase):
    def run_tick(self, state, now, receipt, authority_error=None):
        admission = state.with_suffix('.admission.json')
        if not admission.exists():
            admission.write_text(json.dumps({'version': 1, 'boot_id': 'fixture-boot',
                'monotonic_at': now - 60, 'wall_slot': int((now - 60) // 60),
                'cooldown_seconds': 30}))
        argv = ['sync', '--native-authority-stdin', '--state', str(state), '--actor', 'tracker-workitems',
                '--source', 'seeds:aegis', '--publish-status']
        reader = SimpleNamespace(board=SimpleNamespace(graph='https://seeds.local/project/aegis'))
        stdin = SimpleNamespace(buffer=io.BytesIO(b'{}'))
        with patch.object(sys, 'argv', argv), patch.object(sys, 'stdin', stdin), \
                patch.object(sync.time, 'time', return_value=now), \
                patch.object(sync, 'native_admission_clock', return_value=(now, 'fixture-boot'), create=True), \
                patch.object(seed_work_items, 'from_authority',
                             **({'side_effect': authority_error} if authority_error else {'return_value': reader})), \
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


    def test_an_early_failure_never_erases_the_carried_success(self):
        """ian's falsifier: OK at 1100, then a failure BEFORE the receipt read."""
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'seeds-state.sqlite3'
            ok = {'status': 'OK', 'requests': 3, 'writes': 1, 'verified': 1, 'cursor': 42, 'backlog': 0}
            self.assertEqual(self.run_tick(state, 1100.0, ok)[0], 0)
            for now in (1200.0, 1300.0):
                code, text = self.run_tick(state, now, ok, authority_error=ValueError('authority unreadable'))
                self.assertEqual(code, 2)
                self.assertIn('camayoc_workitem_ingress_last_success_timestamp_seconds 1100', text)
            receipt = json.loads(state.with_suffix('.receipt.json').read_text())
            self.assertEqual(receipt['last_success_at'], 1100.0)


class NativeBusyDiagnostics(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / 'seeds-state.sqlite3'
        self.state.write_bytes(b'exact retained custody fixture')
        self.receipt = self.state.with_suffix('.receipt.json')
        self.receipt.write_text(json.dumps({'attempted_at': 1000.0,
                                          'last_success_at': 900.0,
                                          'status': 'UNKNOWN', 'cursor': 41}))
        self.admission = self.state.with_suffix('.admission.json')
        self.admission.write_text(json.dumps({'version': 1, 'boot_id': 'fixture-boot',
            'monotonic_at': 1000.0, 'wall_slot': 16, 'cooldown_seconds': 30}))

    def run_native(self, now, *, monotonic=None, boot='fixture-boot', clock_error=None,
                   tick_error=None):
        argv = ['sync', '--native-authority-stdin', '--state', str(self.state),
                '--actor', 'fixture', '--source', 'urn:fixture', '--publish-status']
        reader = SimpleNamespace(board=SimpleNamespace(graph='urn:fixture:board'))
        journal = SimpleNamespace(db=SimpleNamespace(close=Mock()))
        output = io.StringIO()
        before = (self.state.read_bytes(), self.receipt.read_bytes() if self.receipt.exists() else None)
        before_admission = self.admission.read_bytes() if self.admission.exists() else None
        with (patch.object(sys, 'argv', argv),
              patch.object(sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(b'{}'))),
              patch.object(sync.time, 'time', return_value=now),
              patch.object(sync, 'native_admission_clock',
                           return_value=(now if monotonic is None else monotonic, boot),
                           side_effect=clock_error, create=True),
              patch.object(seed_work_items, 'from_authority', return_value=reader) as authority,
              patch.object(seed_change_custody, 'Journal', return_value=journal) as opened,
              patch.object(seed_work_item_sync, 'tick', return_value={
                  'status': 'OK', 'requests': 0, 'writes': 0, 'verified': 0},
                           side_effect=tick_error) as tick,
              patch.object(sync.camayoc_metrics, 'push', return_value=(True, '')) as push,
              redirect_stdout(output)):
            code = sync.main()
        return SimpleNamespace(code=code, output=json.loads(output.getvalue()),
                               authority=authority, journal=opened, tick=tick, push=push,
                               before=before, before_admission=before_admission)

    def assert_busy_preserves_state(self, result, reason, *, admission_unchanged=True):
        self.assertEqual(result.code, 0)
        self.assertEqual(result.output.get('busy_reason'), reason)
        self.assertEqual(result.output['status'], 'BUSY')
        self.assertEqual((result.output['requests'], result.output['writes']), (0, 0))
        self.assertEqual((self.state.read_bytes(),
                          self.receipt.read_bytes() if self.receipt.exists() else None), result.before)
        result.tick.assert_not_called()
        result.push.assert_not_called()
        if admission_unchanged:
            self.assertEqual(self.admission.read_bytes(), result.before_admission)

    def test_real_lock_contention_names_its_reason_without_opening_custody(self):
        with self.state.with_suffix('.lock').open('a') as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.run_native(1061.0)
        self.assert_busy_preserves_state(result, 'lock-held')
        result.authority.assert_not_called()
        result.journal.assert_not_called()

    def test_interval_guard_names_its_reason_without_refreshing_the_receipt(self):
        result = self.run_native(1019.999)
        self.assert_busy_preserves_state(result, 'interval-not-due')
        result.journal.assert_called_once()

    def test_exact_interval_boundary_still_runs_the_normal_tick(self):
        result = self.run_native(1060.0)
        self.assertEqual((result.code, result.output['status']), (0, 'OK'))
        self.assertNotIn('busy_reason', result.output)
        result.tick.assert_called_once()
        result.push.assert_called_once()
        self.assertEqual(json.loads(self.receipt.read_text())['attempted_at'], 1060.0)

    def assert_admitted(self, result, now):
        self.assertEqual((result.code, result.output['status']), (0, 'OK'))
        result.tick.assert_called_once()
        result.push.assert_called_once()
        saved = json.loads(self.receipt.read_text())
        self.assertEqual((saved['attempted_at'], saved['last_success_at']), (now, now))

    def test_new_minute_below_thirty_second_floor_preserves_state(self):
        # 1000 is in minute16; 1029.999 is in minute17, but too close.
        self.assert_busy_preserves_state(self.run_native(1029.999), 'interval-not-due')

    def test_new_minute_at_exact_thirty_second_floor_runs(self):
        self.assert_admitted(self.run_native(1030.0), 1030.0)

    def test_same_minute_duplicate_after_floor_elapsed_preserves_state(self):
        previous = json.loads(self.receipt.read_text())
        previous['attempted_at'] = 0.0
        self.receipt.write_text(json.dumps(previous))
        clock = json.loads(self.admission.read_text())
        clock.update(monotonic_at=0.0, wall_slot=0)
        self.admission.write_text(json.dumps(clock))
        self.assert_admitted(self.run_native(1020.0), 1020.0)
        # Both are in minute17. The 30-second floor alone would allow this.
        self.assert_busy_preserves_state(self.run_native(1059.999), 'interval-not-due')

    def test_exact_minute_boundary_with_floor_satisfied_runs(self):
        previous = json.loads(self.receipt.read_text())
        previous['attempted_at'] = 1050.0
        self.receipt.write_text(json.dumps(previous))
        self.assert_admitted(self.run_native(1080.0), 1080.0)

    def test_jittered_scheduled_minutes_each_admit_once(self):
        for now in (1060.1, 1120.0, 1180.2, 1240.0):
            with self.subTest(now=now):
                self.assert_admitted(self.run_native(now), now)

    def test_clock_regression_preserves_receipt_and_success(self):
        for now in (999.999, 959.999):
            with self.subTest(now=now):
                self.assert_busy_preserves_state(
                    self.run_native(now, monotonic=1060.0), 'interval-not-due')

    def test_clock_jump_does_not_replay_missed_minutes(self):
        self.assert_admitted(self.run_native(4620.0), 4620.0)
        self.assert_busy_preserves_state(self.run_native(4659.999), 'interval-not-due')

    def test_forward_wall_step_cannot_bypass_physical_floor(self):
        self.assert_busy_preserves_state(
            self.run_native(10000.0, monotonic=1029.999), 'interval-not-due')
        self.assert_admitted(self.run_native(10000.0, monotonic=1030.0), 10000.0)

    def test_missing_clock_bootstraps_full_sixty_seconds_without_freshness(self):
        self.admission.unlink()
        result = self.run_native(1060.0, monotonic=2000.0)
        self.assert_busy_preserves_state(result, 'interval-not-due', admission_unchanged=False)
        self.assertEqual(result.output['admission_reason'], 'bootstrap')
        self.assert_busy_preserves_state(
            self.run_native(1120.0, monotonic=2059.999), 'interval-not-due')
        self.assert_admitted(self.run_native(1120.0, monotonic=2060.0), 1120.0)

    def test_reboot_does_not_compare_monotonic_clocks_across_boots(self):
        result = self.run_native(1060.0, monotonic=10.0, boot='next-boot')
        self.assert_busy_preserves_state(result, 'interval-not-due', admission_unchanged=False)
        self.assert_busy_preserves_state(
            self.run_native(1120.0, monotonic=69.999, boot='next-boot'), 'interval-not-due')
        self.assert_admitted(self.run_native(1120.0, monotonic=70.0, boot='next-boot'), 1120.0)

    def test_invalid_clock_state_reestablishes_quiet_period_not_success(self):
        for invalid in (b'{', b'{}', b'x' * 4097):
            with self.subTest(invalid=invalid[:10]):
                self.admission.write_bytes(invalid)
                result = self.run_native(1060.0, monotonic=2000.0)
                self.assert_busy_preserves_state(result, 'interval-not-due', admission_unchanged=False)
                self.assertEqual(json.loads(self.admission.read_text())['cooldown_seconds'], 60)

    def test_same_boot_monotonic_regression_reestablishes_quiet_period(self):
        result = self.run_native(1060.0, monotonic=999.0)
        self.assert_busy_preserves_state(result, 'interval-not-due', admission_unchanged=False)
        self.assertEqual(result.output['admission_reason'], 'bootstrap')

    def test_reservation_survives_missing_receipt_after_crash(self):
        self.assert_admitted(self.run_native(1060.0), 1060.0)
        self.receipt.unlink()
        # A second process still sees the reserved slot even if a crash lost its receipt.
        self.assert_busy_preserves_state(self.run_native(1079.999), 'interval-not-due')

    def test_unreadable_clock_does_not_admit_or_refresh(self):
        result = self.run_native(1060.0, clock_error=OSError('fixture unavailable'))
        self.assert_busy_preserves_state(result, 'interval-not-due')
        self.assertEqual(result.output['admission_reason'], 'clock-unavailable')

    def test_failed_tick_consumes_reservation_but_carries_previous_success(self):
        result = self.run_native(1030.0, tick_error=ValueError('fixture tick failure'))
        self.assertEqual(result.code, 2)
        result.tick.assert_called_once()
        self.assertEqual(json.loads(self.receipt.read_text())['last_success_at'], 900.0)
        self.assert_busy_preserves_state(
            self.run_native(1080.0, monotonic=1059.999), 'interval-not-due')

    def test_unreadable_metadata_starts_full_quiet_period(self):
        original = Path.open

        def unreadable(path, *args, **kwargs):
            if path == self.admission and not args and not kwargs:
                raise OSError('fixture unreadable')
            return original(path, *args, **kwargs)

        with patch.object(Path, 'open', unreadable):
            result = self.run_native(1060.0)
        self.assert_busy_preserves_state(result, 'interval-not-due', admission_unchanged=False)
        self.assertEqual(json.loads(self.admission.read_text())['cooldown_seconds'], 60)

    def test_failed_reservation_write_does_not_enter_tick(self):
        original = sync.save

        def unavailable(path, value):
            if path == self.admission:
                raise OSError('fixture cannot reserve')
            return original(path, value)

        with patch.object(sync, 'save', unavailable):
            result = self.run_native(1060.0)
        self.assert_busy_preserves_state(result, 'interval-not-due')
        self.assertEqual(result.output['admission_reason'], 'state-unavailable')

    def test_reservation_is_visible_before_tick_effects(self):
        def inspect_reservation(*args, **kwargs):
            clock = json.loads(self.admission.read_text())
            self.assertEqual((clock['monotonic_at'], clock['wall_slot'], clock['cooldown_seconds']),
                             (1030.0, 17, 30))
            return {'status': 'OK', 'requests': 0, 'writes': 0, 'verified': 0}

        self.assert_admitted(self.run_native(1030.0, tick_error=inspect_reservation), 1030.0)


if __name__ == '__main__':
    unittest.main()
