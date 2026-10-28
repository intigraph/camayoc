"""Requires the gshu.7 buffer plus the gshu.4 capture producer."""
import base64
import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from camayoc_cli import capture, capture_spool, cli, memory, offline, quipu


def fixture(**values):
    return dict(at='2026-10-28T12:00:00Z', **values)


class CaptureFifoTests(unittest.TestCase):
    def setUp(self):
        isolation = mock.patch.dict('os.environ', {'QUIPU_SERVER': 'http://127.0.0.1:0',
            'QUIPU_AUTH_TOKEN': '', 'QUIPU_AUTH_TOKEN_FILE': '/nonexistent-test-token',
            'CAMAYOC_NOW': '2026-10-28T12:00:00Z'})
        isolation.start()
        self.addCleanup(isolation.stop)
        # Even an accidental unmocked client cannot reach an operator endpoint.
        network = mock.patch('urllib.request.urlopen', side_effect=AssertionError('no test network'))
        network.start()
        self.addCleanup(network.stop)

    def test_refused_capture_is_quarantined_and_later_entry_can_replay(self):
        with tempfile.TemporaryDirectory() as root:
            env = {'CAMAYOC_NOW': '2026-10-28T12:00:00Z', 'XDG_STATE_HOME': root, 'QUIPU_SERVER': 'http://127.0.0.1:0'}
            capture.spool(fixture(agent='first', text='first', hash='hash1'), env)
            capture.spool(fixture(agent='second', text='second', hash='hash2'), env)
            client = mock.Mock()
            client.knot.side_effect = [quipu.Refused(['refused']), None]
            with mock.patch.object(capture, 'turtle', return_value='fixture'):
                result = capture.flush(client=client, env=env)
            self.assertEqual(1, result.code)
            self.assertEqual(2, client.knot.call_count)
            self.assertEqual(1, result.data['written'])
            self.assertEqual(1, result.data['quarantined'])
            self.assertEqual([], capture.spooled(env))

    def test_capture_append_during_rpc_survives_ack(self):
        with tempfile.TemporaryDirectory() as root:
            env = {'CAMAYOC_NOW': '2026-10-28T12:00:00Z', 'XDG_STATE_HOME': root, 'QUIPU_SERVER': 'http://127.0.0.1:0'}
            capture.spool(fixture(agent='first', text='first', hash='hash1'), env)
            client = mock.Mock()
            client.knot.side_effect = lambda *a: capture.spool(fixture(agent='late', text='late', hash='hash2'), env)
            with mock.patch.object(capture, 'turtle', return_value='fixture'):
                result = capture.flush(client=client, env=env, limit=1)
            self.assertEqual(1, result.data['written'])
            self.assertEqual(['late'], [e['agent'] for e in capture.spooled(env)])

    def test_torn_capture_is_quarantined_without_rpc(self):
        with tempfile.TemporaryDirectory() as root:
            env = {'CAMAYOC_NOW': '2026-10-28T12:00:00Z', 'XDG_STATE_HOME': root, 'QUIPU_SERVER': 'http://127.0.0.1:0'}
            path = capture.spool_path(env)
            path.parent.mkdir()
            path.write_text('{partial')
            client = mock.Mock()
            result = capture.flush(client=client, env=env)
            self.assertEqual(1, result.code)
            self.assertEqual(0, offline.capture_length(env))
            client.knot.assert_not_called()
            self.assertEqual(1, result.data['quarantined'])

    def test_old_unbound_inflight_is_quarantined_before_new_captures(self):
        with tempfile.TemporaryDirectory() as root:
            env = {'CAMAYOC_NOW': '2026-10-28T12:00:00Z', 'XDG_STATE_HOME': root, 'QUIPU_SERVER': 'http://127.0.0.1:0'}
            capture.state_dir(env).mkdir()
            old = fixture(agent='old', text='legacy', hash='hash0')
            capture.inflight_path(env).write_bytes(capture._line(old))
            self.assertTrue(capture.spool(fixture(agent='new', text='new', hash='hash1'), env))
            client = mock.Mock()
            with mock.patch.object(capture, 'turtle', return_value='fixture'):
                result = capture.flush(client=client, env=env)
            self.assertEqual(1, result.code)
            self.assertEqual(1, client.knot.call_count)
            self.assertEqual(0, offline.capture_length(env))
            self.assertEqual(1, result.data['quarantined'])

    def test_changed_destination_quarantines_capture_without_rpc(self):
        with tempfile.TemporaryDirectory() as root:
            env = {'CAMAYOC_NOW': '2026-10-28T12:00:00Z', 'XDG_STATE_HOME': root, 'QUIPU_SERVER': 'http://127.0.0.1:0'}
            self.assertTrue(capture.spool(fixture(agent='old', text='old', hash='hash0'), env))
            client = mock.Mock()
            result = capture.flush(client=client, env={**env, 'QUIPU_SERVER': 'http://127.0.0.1:1'})
            self.assertEqual(1, result.code)
            self.assertEqual(0, client.knot.call_count)
            self.assertEqual(0, offline.capture_length(env))
            self.assertEqual(1, result.data['quarantined'])

    def test_ack_failure_keeps_uncertain_capture_pending(self):
        with tempfile.TemporaryDirectory() as root:
            env = {'CAMAYOC_NOW': '2026-10-28T12:00:00Z', 'XDG_STATE_HOME': root, 'QUIPU_SERVER': 'http://127.0.0.1:0'}
            self.assertTrue(capture.spool(fixture(agent='old', text='old', hash='hash0'), env))
            with mock.patch.object(capture, 'turtle', return_value='fixture'), \
                    mock.patch.object(capture_spool, 'acknowledge', side_effect=OSError('fixture')):
                result = capture.flush(client=mock.Mock(), env=env)
            self.assertEqual(1, result.code)
            self.assertEqual(0, result.data['written'])
            self.assertEqual(1, result.data['spooled'])

    def test_shared_replay_limit_also_bounds_capture_work(self):
        from camayoc_cli import memory
        with tempfile.TemporaryDirectory() as root:
            env = {'CAMAYOC_NOW': '2026-10-28T12:00:00Z', 'XDG_STATE_HOME': root, 'QUIPU_SERVER': 'http://127.0.0.1:0'}
            for n in range(2):
                self.assertTrue(capture.spool(fixture(agent=str(n), text=str(n), hash=str(n)), env))
            client = mock.Mock()
            with mock.patch.object(memory, 'flush', return_value=memory.Outcome(0, data={'replayed': 1})), \
                    mock.patch.object(capture, 'client_for', return_value=client), \
                    mock.patch.object(capture, 'turtle', return_value='fixture'):
                result = memory.flush_all(env=env, limit=2)
            self.assertEqual(1, client.knot.call_count)
            self.assertEqual(1, result.data['captures'])

    def test_capture_context_keeps_origin_but_no_credentials(self):
        with tempfile.TemporaryDirectory() as root:
            env = {'CAMAYOC_NOW': '2026-10-28T12:00:00Z', 'XDG_STATE_HOME': root, 'QUIPU_SERVER': 'http://127.0.0.1:0',
                   'SHANTY_AGENT': 'origin', 'QUIPU_AUTH_TOKEN': 'fixture-credential'}
            self.assertTrue(capture.spool(fixture(agent='origin', session='source-session',
                                              text='fixture', hash='hash0'), env))
            entry, = capture.spooled(env)
            context = entry['_offline_context']
            self.assertEqual('source-session', context['env']['QUIPU_SESSION'])
            self.assertEqual('origin', context['env']['QUIPU_AGENT'])
            self.assertNotIn('QUIPU_AUTH_TOKEN', context['env'])
            self.assertNotIn('fixture-credential', capture.spool_path(env).read_text())

    def test_empty_destination_never_falls_back_to_process_endpoint(self):
        with tempfile.TemporaryDirectory() as root:
            env = {'CAMAYOC_NOW': '2026-10-28T12:00:00Z', 'XDG_STATE_HOME': root, 'QUIPU_SERVER': ''}
            self.assertTrue(capture.spool(fixture(agent='old', text='old', hash='hash0'), env))
            with mock.patch.object(quipu, 'Client', side_effect=AssertionError('must not open a client')):
                result = capture.flush(env=env)
            self.assertEqual(1, result.code)
            self.assertEqual(0, result.data['spooled'])
            self.assertEqual(1, result.data['quarantined'])

    def test_seven_day_limit_quarantines_expired_and_replays_boundary(self):
        with tempfile.TemporaryDirectory() as root:
            env = {'XDG_STATE_HOME': root, 'QUIPU_SERVER': 'http://127.0.0.1:0',
                   'CAMAYOC_NOW': '2026-11-04T12:00:00Z'}
            old = fixture(agent='expired', text='old', hash='hash-old')
            old['at'] = '2026-10-28T11:59:59Z'
            capture.spool(old, env)
            capture.spool(fixture(agent='boundary', text='boundary', hash='hash-boundary'), env)
            client = mock.Mock()
            with mock.patch.object(capture, 'turtle', return_value='fixture'):
                out = capture.flush(client=client, env=env)
            self.assertEqual((1, 1, 0), (out.data['written'], out.data['quarantined'], out.data['spooled']))
            rows = capture_spool.quarantined(capture.spool_path(env))
            self.assertEqual('expired', rows[0]['reason'])
            self.assertIn(b'hash-old', base64.b64decode(rows[0]['raw_base64']))
            client.knot.assert_called_once()

    def test_torn_append_is_isolated_and_later_capture_is_delivered(self):
        with tempfile.TemporaryDirectory() as root:
            env = {'XDG_STATE_HOME': root, 'QUIPU_SERVER': 'http://127.0.0.1:0',
                   'CAMAYOC_NOW': '2026-10-28T12:00:00Z'}
            path = capture.spool_path(env)
            path.parent.mkdir()
            path.write_bytes(b'\xff{torn')
            capture.spool(fixture(agent='good', text='good', hash='hash-good'), env)
            with mock.patch.object(capture, 'turtle', return_value='fixture'):
                out = capture.flush(client=mock.Mock(), env=env)
            self.assertEqual((1, 1, 0), (out.data['written'], out.data['quarantined'], out.data['spooled']))
            row, = capture_spool.quarantined(path)
            self.assertEqual(b'\xff{torn\n', base64.b64decode(row['raw_base64']))

    def test_quarantine_ack_failure_retries_archive_without_duplicate(self):
        with tempfile.TemporaryDirectory() as root:
            env = {'XDG_STATE_HOME': root, 'QUIPU_SERVER': 'http://127.0.0.1:0',
                   'CAMAYOC_NOW': '2026-10-28T12:00:00Z'}
            path = capture.spool_path(env)
            path.parent.mkdir()
            path.write_bytes(b'{torn')
            with mock.patch.object(capture_spool, 'acknowledge', side_effect=OSError('disk')):
                first = capture.flush(client=mock.Mock(), env=env)
            self.assertIsNone(first.data['spooled'])  # original torn bytes still pending
            second = capture.flush(client=mock.Mock(), env=env)
            self.assertEqual((0, 1), (second.data['spooled'], second.data['quarantined']))
            self.assertEqual(1, len(capture_spool.quarantined(path)))

    def test_archive_failure_preserves_head_and_tail(self):
        with tempfile.TemporaryDirectory() as root:
            env = {'XDG_STATE_HOME': root, 'QUIPU_SERVER': 'http://127.0.0.1:0',
                   'CAMAYOC_NOW': '2026-10-28T12:00:00Z'}
            for who in ['first', 'later']:
                capture.spool(fixture(agent=who, text=who, hash=who), env)
            client = mock.Mock()
            client.knot.side_effect = quipu.Refused(['fixture'])
            with mock.patch.object(capture, 'turtle', return_value='fixture'), \
                    mock.patch.object(capture_spool, 'quarantine', side_effect=OSError('disk')):
                out = capture.flush(client=client, env=env)
            self.assertEqual(2, out.data['spooled'])
            client.knot.assert_called_once()
            self.assertEqual(['first', 'later'], [r['agent'] for r in capture.spooled(env)])

    def test_uncertain_rpc_still_stops_ordered_replay(self):
        with tempfile.TemporaryDirectory() as root:
            env = {'XDG_STATE_HOME': root, 'QUIPU_SERVER': 'http://127.0.0.1:0',
                   'CAMAYOC_NOW': '2026-10-28T12:00:00Z'}
            for who in ['first', 'later']:
                capture.spool(fixture(agent=who, text=who, hash=who), env)
            client = mock.Mock()
            client.knot.side_effect = quipu.Unreachable('fixture')
            with mock.patch.object(capture, 'turtle', return_value='fixture'):
                out = capture.flush(client=client, env=env)
            self.assertEqual((2, 0), (out.data['spooled'], out.data['quarantined']))
            client.knot.assert_called_once()

    def test_quarantine_count_inspection_and_explicit_clear_are_local(self):
        with tempfile.TemporaryDirectory() as root:
            env = {'XDG_STATE_HOME': root, 'QUIPU_SERVER': 'http://127.0.0.1:0'}
            path = capture.spool_path(env)
            path.parent.mkdir()
            key = capture_spool.quarantine(path, b'private body', 'malformed', '2026-10-28T12:00:00Z')
            archive = capture_spool.quarantine_dir(path) / (key + '.json')
            before = (archive.read_bytes(), archive.stat().st_mtime_ns)
            self.assertEqual(1, offline.backlog(env)['quarantined_captures'])
            output = io.StringIO()
            with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(output):
                self.assertEqual(0, cli.main(['capture', 'quarantine', '--json']))
            self.assertNotIn('private body', output.getvalue())
            self.assertNotIn('raw_base64', output.getvalue())
            self.assertEqual(1, json.loads(output.getvalue())['quarantined'])
            self.assertEqual(before, (archive.read_bytes(), archive.stat().st_mtime_ns))
            with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, cli.main(['capture', 'quarantine', '--clear']))
            self.assertEqual(0, offline.backlog(env)['quarantined_captures'])

    def test_hook_append_reads_one_tail_byte_for_small_and_large_spools(self):
        for size in [100, 8 * 1024 * 1024]:
            with self.subTest(size=size), tempfile.TemporaryDirectory() as root:
                env = {'XDG_STATE_HOME': root, 'QUIPU_SERVER': 'http://127.0.0.1:0',
                       'CAMAYOC_NOW': '2026-10-28T12:00:00Z', 'CAMAYOC_CAPTURE': '1'}
                path = capture.spool_path(env)
                path.parent.mkdir()
                path.write_bytes(b' ' * (size - 1) + b'\n')
                with mock.patch.object(offline, '_read', side_effect=AssertionError('no spool scan')), \
                        mock.patch.object(capture_spool.os, 'read', wraps=os.read) as read, \
                        mock.patch.object(capture, 'flush_detached'):
                    result = capture.hook('user-prompt-submit', {'prompt': 'long enough prompt to capture'}, env)
                self.assertEqual(['spooled'], result.lines)
                self.assertEqual([1], [call.args[1] for call in read.call_args_list])

    def test_flush_hook_only_spawns_worker_without_reading_spool(self):
        with mock.patch.object(memory, 'flush_all', side_effect=AssertionError('hook must detach')), \
                mock.patch.object(cli.subprocess, 'Popen') as spawn:
            self.assertEqual(0, cli.main(['flush', '--hook']))
        self.assertEqual(['flush', '--hook-worker'], spawn.call_args.args[0][-2:])
        with mock.patch.object(cli.subprocess, 'Popen', side_effect=OSError('cannot spawn')):
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(0, cli.main(['flush', '--hook']))

    def test_quarantine_visits_count_against_replay_limit(self):
        with tempfile.TemporaryDirectory() as root:
            env = {'XDG_STATE_HOME': root, 'QUIPU_SERVER': 'http://127.0.0.1:0'}
            path = capture.spool_path(env)
            path.parent.mkdir()
            path.write_bytes(b'{bad\n{worse\n')
            out = capture.flush(client=mock.Mock(), env=env, limit=1)
            self.assertEqual(1, out.data['quarantined'])
            self.assertEqual(b'{worse\n', capture.inflight_path(env).read_bytes())

    def test_note_never_claims_quarantine_is_delivery(self):
        with tempfile.TemporaryDirectory() as root:
            env = {'XDG_STATE_HOME': root, 'XDG_CONFIG_HOME': root,
                   'QUIPU_SERVER': 'http://127.0.0.1:0', 'CAMAYOC_NOW': '2026-10-28T12:00:00Z'}
            client = mock.Mock()
            client.knot.side_effect = quipu.Refused(['fixture'])
            out = capture.note('an explicit note refused by the fixture', env=env, client=client)
            self.assertEqual((3, 'quarantined'), (out.code, out.data['status']))

    def test_unreadable_quarantine_is_unknown_and_clear_refuses(self):
        with tempfile.TemporaryDirectory() as root:
            env = {'XDG_STATE_HOME': root, 'QUIPU_SERVER': 'http://127.0.0.1:0'}
            path = capture.spool_path(env)
            directory = capture_spool.quarantine_dir(path)
            directory.mkdir(parents=True)
            archive = directory / ('a' * 64 + '.json')
            archive.write_bytes(b'{partial')
            before = archive.stat().st_mtime_ns
            self.assertIsNone(offline.backlog(env)['quarantined_captures'])
            self.assertEqual(1, capture.quarantine(env, clear=True).code)
            self.assertEqual((b'{partial', before), (archive.read_bytes(), archive.stat().st_mtime_ns))
