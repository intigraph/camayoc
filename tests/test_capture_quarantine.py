"""Local quarantine recovery/retention only. Every network call is a test error."""
import base64
import contextlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from camayoc_cli import capture, capture_spool, cli, memory, offline

STAMP = '2026-10-28T12:00:00Z'


class QuarantineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = {'HOME': self.tmp.name, 'XDG_STATE_HOME': self.tmp.name,
                    'XDG_CONFIG_HOME': self.tmp.name, 'QUIPU_SERVER': 'http://127.0.0.1:0',
                    'QUIPU_AUTH_TOKEN': '', 'QUIPU_AUTH_TOKEN_FILE': self.tmp.name + '/no-token',
                    'CAMAYOC_NOW': STAMP, 'SHANTY_AGENT': 'fixture'}
        isolation = mock.patch.dict(os.environ, self.env)
        isolation.start()
        self.addCleanup(isolation.stop)
        network = mock.patch('urllib.request.urlopen', side_effect=AssertionError('NO TEST NETWORK'))
        network.start()
        self.addCleanup(network.stop)
        self.active = capture.spool_path(self.env)
        self.active.parent.mkdir(parents=True)
        self.prefix = capture.inflight_path(self.env)

    def entry(self, text='fixture capture text', **updates):
        entry = capture.build(text, 'explicit', session='original-session', env=self.env)
        entry['_offline_context'] = memory._queued_context(self.env)
        entry.update(updates)
        return entry

    def archive(self, entry, reason='destination', at=STAMP):
        raw = capture._line(entry) if isinstance(entry, dict) else entry
        return capture_spool.quarantine(self.active, raw, reason, at)

    def test_requeue_restores_destination_and_origin_at_pending_tail_without_rpc(self):
        original = self.entry()
        capture.spool(original, self.env)
        original, = capture.spooled(self.env)  # includes producer-frozen origin
        wrong = dict(self.env, QUIPU_SERVER='http://127.0.0.1:1')
        with mock.patch.object(capture.quipu, 'Client', side_effect=AssertionError('no network client')):
            out = capture.flush(env=wrong)
            self.assertEqual(1, out.data['quarantined'])
            mismatch = capture.quarantine(wrong, requeue=True)
            self.assertEqual((0, 1), (mismatch.data['requeued'], mismatch.data['quarantined']))
            self.assertEqual({'destination': 1}, mismatch.data['skipped'])
            capture.spool(self.entry('already waiting'), self.env)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(0, cli.main(['capture', 'quarantine', '--requeue', '--json']))
        self.assertEqual(1, json.loads(output.getvalue())['requeued'])
        pending = capture.spooled(self.env)
        self.assertEqual(['already waiting', original['text']], [r['text'] for r in pending])
        recovered = dict(pending[-1])
        recovered.pop('_requeued_from')
        self.assertEqual(original, recovered)
        self.assertEqual(0, offline.backlog(self.env)['quarantined_captures'])

    def test_requeue_never_retargets_another_plane(self):
        self.archive(self.entry())
        env = dict(self.env, CAMAYOC_PLANE_NS='https://example.invalid/other-plane/')
        out = capture.quarantine(env, requeue=True)
        self.assertEqual((0, 1), (out.data['requeued'], out.data['quarantined']))
        self.assertEqual([], capture.spooled(self.env))

    def test_expired_malformed_and_unbound_archives_stay_out_of_pending(self):
        self.archive(self.entry(at='2026-10-01T12:00:00Z'), 'expired')
        self.archive(b'{torn', 'malformed')
        self.archive(self.entry(_offline_context={}), 'destination')
        self.archive(self.entry('previously refused, now eligible'), 'refused')
        out = capture.quarantine(self.env, requeue=True)
        self.assertEqual((1, 3), (out.data['requeued'], out.data['quarantined']))
        self.assertEqual({'expired': 1, 'malformed': 1, 'destination': 1}, out.data['skipped'])

    def test_pending_capacity_refusal_retains_archive(self):
        key = self.archive(self.entry())
        with mock.patch.object(offline, 'MAX_BYTES', 0):
            out = capture.quarantine(self.env, requeue=True)
        self.assertTrue(out.data['capacity_blocked'])
        self.assertEqual((0, 1), (out.data['requeued'], out.data['quarantined']))
        self.assertEqual(key, capture_spool.quarantined(self.active)[0]['id'])
        self.assertEqual([], capture.spooled(self.env))

    def test_failed_append_never_deletes_archive(self):
        key = self.archive(self.entry())
        with mock.patch.object(capture_spool, 'append', side_effect=OSError('disk')):
            out = capture.quarantine(self.env, requeue=True)
        self.assertEqual(1, out.code)
        self.assertEqual(key, capture_spool.quarantined(self.active)[0]['id'])
        self.assertEqual([], capture.spooled(self.env))

    def test_failed_archive_removal_retries_without_second_pending_copy(self):
        key = self.archive(self.entry())
        archive = capture_spool.quarantine_dir(self.active) / (key + '.json')
        unlink = Path.unlink
        def fail_archive(path, *args, **kwargs):
            if path == archive:
                raise OSError('fixture unlink failure')
            return unlink(path, *args, **kwargs)
        with mock.patch.object(Path, 'unlink', fail_archive):
            self.assertEqual(1, capture.quarantine(self.env, requeue=True).code)
        self.assertEqual(1, len(capture.spooled(self.env)))
        out = capture.quarantine(self.env, requeue=True)
        self.assertEqual((1, 0), (out.data['requeued'], out.data['quarantined']))
        self.assertEqual(1, len(capture.spooled(self.env)))

    def test_busy_replayer_leaves_quarantine_and_pending_unchanged(self):
        self.archive(self.entry())
        with capture._flush_lock(self.env) as held:
            self.assertTrue(held)
            out = capture.quarantine(self.env, requeue=True)
        self.assertEqual(1, out.code)
        self.assertEqual(1, len(capture_spool.quarantined(self.active)))
        self.assertEqual([], capture.spooled(self.env))

    def test_age_boundary_and_read_only_inspection(self):
        self.archive(b'older', 'malformed', '2026-10-21T11:59:59Z')
        self.archive(b'boundary', 'malformed', '2026-10-21T12:00:00Z')
        directory = capture_spool.quarantine_dir(self.active)
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in directory.glob('*.json')}
        self.assertEqual(2, capture.quarantine(self.env).data['quarantined'])
        self.assertEqual(before, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in directory.glob('*.json')})
        out = capture.flush(client=mock.Mock(), env=self.env, limit=0)
        self.assertEqual(1, out.data['quarantine_retention']['expired'])
        row, = capture_spool.quarantined(self.active)
        self.assertEqual(b'boundary', base64.b64decode(row['raw_base64']))

    def test_count_capacity_evicts_oldest_archive_with_visible_count(self):
        counts = dict(expired=0, capacity=0, bytes=0)
        with mock.patch.object(capture_spool, 'MAX_QUARANTINE_ENTRIES', 2):
            for i in range(3):
                capture_spool.quarantine(self.active, str(i).encode(), 'malformed',
                    f'2026-10-28T12:00:0{i}Z', retention=counts)
        self.assertEqual(1, counts['capacity'])
        self.assertEqual([b'1', b'2'], sorted(base64.b64decode(r['raw_base64']) for r in capture_spool.quarantined(self.active)))

    def test_byte_capacity_counts_encoded_archive_bytes(self):
        counts = dict(expired=0, capacity=0, bytes=0)
        with mock.patch.object(capture_spool, 'MAX_QUARANTINE_BYTES', 350):
            for i in range(3):
                capture_spool.quarantine(self.active, bytes([65 + i]) * 40, 'malformed',
                    f'2026-10-28T12:00:0{i}Z', retention=counts)
        directory = capture_spool.quarantine_dir(self.active)
        self.assertLessEqual(sum(p.stat().st_size for p in directory.glob('*.json')), 350)
        self.assertEqual(2, counts['capacity'])

    def test_oversized_archive_refuses_without_evicting_existing_evidence(self):
        self.archive(b'keep', 'malformed')
        before = capture_spool.quarantined(self.active)
        with mock.patch.object(capture_spool, 'MAX_QUARANTINE_BYTES', 1):
            with self.assertRaises(offline.QueueError):
                self.archive(b'too large', 'malformed')
        self.assertEqual(before, capture_spool.quarantined(self.active))

    def test_shared_worker_expires_quarantine_even_with_typed_queue_blocked(self):
        self.archive(b'old', 'malformed', '2026-10-01T12:00:00Z')
        with mock.patch.object(memory, 'flush', return_value=memory.Outcome(1)):
            out = memory.flush_all(env=self.env)
        self.assertEqual(1, out.data['quarantine_retention']['expired'])
        self.assertEqual([], capture_spool.quarantined(self.active))

    def test_clear_and_requeue_are_mutually_exclusive(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.main(['capture', 'quarantine', '--clear', '--requeue'])

    def test_background_maintenance_removes_orphaned_private_staging_payload(self):
        directory = capture_spool.quarantine_dir(self.active)
        directory.mkdir()
        orphan = directory / '.pending-fixture'
        orphan.write_bytes(b'private interrupted payload')
        result = capture_spool.prune(self.active, STAMP)
        self.assertEqual(1, result['orphaned'])
        self.assertFalse(orphan.exists())

    def test_unreadable_quarantine_stays_unknown_without_blocking_typed_replay(self):
        directory = capture_spool.quarantine_dir(self.active)
        directory.mkdir()
        archive = directory / ('a' * 64 + '.json')
        archive.write_bytes(b'{torn')
        with mock.patch.object(memory, 'flush', return_value=memory.Outcome(0)) as typed:
            out = memory.flush_all(env=self.env)
        typed.assert_called_once()
        self.assertEqual(1, out.code)
        self.assertIsNone(out.data['quarantined_captures'])
        self.assertEqual(b'{torn', archive.read_bytes())

    def test_corrupt_archive_metadata_cannot_escape_age_limit(self):
        directory = capture_spool.quarantine_dir(self.active)
        directory.mkdir()
        archive = directory / ('a' * 64 + '.json')
        archive.write_bytes(b'{torn private archive')
        os.utime(archive, (1, 1))
        result = capture_spool.prune(self.active, STAMP)
        self.assertEqual(1, result['expired'])
        self.assertFalse(archive.exists())

    def test_silent_hook_eviction_is_visible_to_separate_cli_until_explicit_clear(self):
        self.archive(b'old private prompt one', 'malformed', '2026-10-01T12:00:00Z')
        self.archive(b'old private prompt two', 'malformed', '2026-10-01T12:00:00Z')
        with mock.patch.object(memory, 'flush', return_value=memory.Outcome(1)), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(0, cli.main(['flush', '--hook-worker']))
        ledger = capture_spool.retention_path(self.active)
        self.assertEqual(0o600, ledger.stat().st_mode & 0o777)
        self.assertNotIn(b'old private prompt', ledger.read_bytes())
        before = (ledger.read_bytes(), ledger.stat().st_mtime_ns)
        def read_cli(*args):
            child = subprocess.run([sys.executable, '-m', 'camayoc_cli', *args],
                cwd=Path(__file__).resolve().parents[1], env=dict(os.environ, **self.env),
                capture_output=True, text=True, timeout=5, check=True)
            return child.stdout
        report = json.loads(read_cli('capture', 'quarantine', '--json'))
        self.assertEqual(0, report['quarantined'])
        self.assertEqual(2, report['retention_totals']['expired'])
        self.assertEqual(STAMP, report['retention_totals']['last_eviction_at'])
        backlog = json.loads(read_cli('backlog'))
        self.assertEqual(2, backlog['retention_totals']['expired'])
        self.assertIn('expired=2', read_cli('capture', 'quarantine'))
        self.assertEqual(before, (ledger.read_bytes(), ledger.stat().st_mtime_ns))
        cleared = json.loads(read_cli('capture', 'quarantine', '--clear', '--json'))
        self.assertEqual(0, cleared['retention_totals']['expired'])
        self.assertIsNone(cleared['retention_totals']['last_eviction_at'])
        self.assertFalse(ledger.exists())

    def test_counter_commit_failure_keeps_durable_eviction_receipt_without_double_count(self):
        self.archive(b'expired private prompt', 'malformed', '2026-10-01T12:00:00Z')
        write = capture_spool._write_retention
        calls = 0
        def fail_counter_commit(*args):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('fixture counter commit failed after unlink')
            return write(*args)
        with mock.patch.object(capture_spool, '_write_retention', side_effect=fail_counter_commit):
            with self.assertRaises(OSError):
                capture_spool.prune(self.active, STAMP)
        self.assertEqual(1, capture_spool.retention_totals(self.active)['expired'])
        self.assertEqual([], capture_spool.quarantined(self.active))
        capture_spool.prune(self.active, STAMP)
        self.assertEqual(1, capture_spool.retention_totals(self.active)['expired'])
        self.assertIsNone(json.loads(capture_spool.retention_path(self.active).read_text())['pending'])

    def test_unwritable_ledger_prevents_unreported_eviction(self):
        self.archive(b'keep until eviction is recorded', 'malformed', '2026-10-01T12:00:00Z')
        before = capture_spool.quarantined(self.active)
        with mock.patch.object(capture_spool, '_write_retention', side_effect=OSError('fixture full disk')):
            with self.assertRaises(OSError):
                capture_spool.prune(self.active, STAMP)
        self.assertEqual(before, capture_spool.quarantined(self.active))
        self.assertEqual(0, capture_spool.retention_totals(self.active)['expired'])

    def test_corrupt_retention_ledger_is_unknown_not_zero(self):
        capture_spool.retention_path(self.active).write_bytes(b'{partial')
        self.assertIsNone(capture.quarantine(self.env).data['retention_totals'])
        self.assertIsNone(offline.backlog(self.env)['retention_totals'])
