"""Requires the gshu.7 buffer plus the gshu.4 capture producer."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from camayoc_cli import capture, offline, quipu


class CaptureFifoTests(unittest.TestCase):
    def test_refused_capture_is_retained_and_later_entries_do_not_overtake(self):
        with tempfile.TemporaryDirectory() as root:
            env = {'XDG_STATE_HOME': root, 'QUIPU_SERVER': 'http://127.0.0.1:0'}
            capture.spool(dict(agent='first', text='first', hash='hash1'), env)
            capture.spool(dict(agent='second', text='second', hash='hash2'), env)
            client = mock.Mock()
            client.knot.side_effect = quipu.Refused(['refused'])
            with mock.patch.object(capture, 'turtle', return_value='fixture'):
                result = capture.flush(client=client, env=env)
            self.assertEqual(1, result.code)
            self.assertEqual(1, client.knot.call_count)
            self.assertEqual(['first', 'second'], [e['agent'] for e in capture.spooled(env)])

    def test_capture_append_during_rpc_survives_ack(self):
        with tempfile.TemporaryDirectory() as root:
            env = {'XDG_STATE_HOME': root, 'QUIPU_SERVER': 'http://127.0.0.1:0'}
            capture.spool(dict(agent='first', text='first', hash='hash1'), env)
            client = mock.Mock()
            client.knot.side_effect = lambda *a: capture.spool(dict(agent='late', text='late', hash='hash2'), env)
            with mock.patch.object(capture, 'turtle', return_value='fixture'):
                result = capture.flush(client=client, env=env, limit=1)
            self.assertEqual(1, result.data['written'])
            self.assertEqual(['late'], [e['agent'] for e in capture.spooled(env)])

    def test_torn_capture_is_unknown_without_rewrite(self):
        with tempfile.TemporaryDirectory() as root:
            env = {'XDG_STATE_HOME': root, 'QUIPU_SERVER': 'http://127.0.0.1:0'}
            path = capture.spool_path(env)
            path.parent.mkdir()
            path.write_text('{partial')
            result = capture.flush(client=mock.Mock(), env=env)
            self.assertEqual(1, result.code)
            self.assertIsNone(offline.capture_length(env))
            self.assertEqual('{partial', path.read_text())

    def test_old_unbound_inflight_is_retained_before_new_captures(self):
        with tempfile.TemporaryDirectory() as root:
            env = {'XDG_STATE_HOME': root, 'QUIPU_SERVER': 'http://127.0.0.1:0'}
            capture.state_dir(env).mkdir()
            old = dict(agent='old', text='legacy', hash='hash0')
            capture.inflight_path(env).write_bytes(capture._line(old))
            self.assertTrue(capture.spool(dict(agent='new', text='new', hash='hash1'), env))
            client = mock.Mock()
            result = capture.flush(client=client, env=env)
            self.assertEqual(1, result.code)
            self.assertEqual(0, client.knot.call_count)
            self.assertEqual(2, offline.capture_length(env))
            self.assertEqual(['old', 'new'], [e['agent'] for e in capture.spooled(env)])

    def test_changed_destination_retains_capture_without_rpc(self):
        with tempfile.TemporaryDirectory() as root:
            env = {'XDG_STATE_HOME': root, 'QUIPU_SERVER': 'http://127.0.0.1:0'}
            self.assertTrue(capture.spool(dict(agent='old', text='old', hash='hash0'), env))
            client = mock.Mock()
            result = capture.flush(client=client, env={**env, 'QUIPU_SERVER': 'http://127.0.0.1:1'})
            self.assertEqual(1, result.code)
            self.assertEqual(0, client.knot.call_count)
            self.assertEqual(1, offline.capture_length(env))

    def test_ack_failure_keeps_uncertain_capture_pending(self):
        with tempfile.TemporaryDirectory() as root:
            env = {'XDG_STATE_HOME': root, 'QUIPU_SERVER': 'http://127.0.0.1:0'}
            self.assertTrue(capture.spool(dict(agent='old', text='old', hash='hash0'), env))
            with mock.patch.object(capture, 'turtle', return_value='fixture'), \
                    mock.patch.object(offline, 'acknowledge', side_effect=OSError('fixture')):
                result = capture.flush(client=mock.Mock(), env=env)
            self.assertEqual(1, result.code)
            self.assertEqual(0, result.data['written'])
            self.assertEqual(1, result.data['spooled'])

    def test_shared_replay_limit_also_bounds_capture_work(self):
        from camayoc_cli import memory
        with tempfile.TemporaryDirectory() as root:
            env = {'XDG_STATE_HOME': root, 'QUIPU_SERVER': 'http://127.0.0.1:0'}
            for n in range(2):
                self.assertTrue(capture.spool(dict(agent=str(n), text=str(n), hash=str(n)), env))
            client = mock.Mock()
            with mock.patch.object(memory, 'flush', return_value=memory.Outcome(0, data={'replayed': 1})), \
                    mock.patch.object(capture, 'client_for', return_value=client), \
                    mock.patch.object(capture, 'turtle', return_value='fixture'):
                result = memory.flush_all(env=env, limit=2)
            self.assertEqual(1, client.knot.call_count)
            self.assertEqual(1, result.data['captures'])

    def test_capture_context_keeps_origin_but_no_credentials(self):
        with tempfile.TemporaryDirectory() as root:
            env = {'XDG_STATE_HOME': root, 'QUIPU_SERVER': 'http://127.0.0.1:0',
                   'SHANTY_AGENT': 'origin', 'QUIPU_AUTH_TOKEN': 'fixture-credential'}
            self.assertTrue(capture.spool(dict(agent='origin', session='source-session',
                                              text='fixture', hash='hash0'), env))
            entry, = capture.spooled(env)
            context = entry['_offline_context']
            self.assertEqual('source-session', context['env']['QUIPU_SESSION'])
            self.assertEqual('origin', context['env']['QUIPU_AGENT'])
            self.assertNotIn('QUIPU_AUTH_TOKEN', context['env'])
            self.assertNotIn('fixture-credential', capture.spool_path(env).read_text())

    def test_empty_destination_never_falls_back_to_process_endpoint(self):
        with tempfile.TemporaryDirectory() as root:
            env = {'XDG_STATE_HOME': root, 'QUIPU_SERVER': ''}
            self.assertTrue(capture.spool(dict(agent='old', text='old', hash='hash0'), env))
            with mock.patch.object(quipu, 'Client', side_effect=AssertionError('must not open a client')):
                result = capture.flush(env=env)
            self.assertEqual(1, result.code)
            self.assertEqual(1, result.data['spooled'])
