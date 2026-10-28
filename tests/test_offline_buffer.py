"""No real store: deterministic replay failures, concurrent appends and hooks."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from camayoc_cli import cli, memory, offline, quipu


class OfflineBufferTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        isolation = mock.patch.dict(os.environ, {
            'QUIPU_SERVER': 'http://127.0.0.1:0', 'QUIPU_AUTH_TOKEN': '',
            'QUIPU_AUTH_TOKEN_FILE': str(Path(self.tmp.name) / 'no-token'),
            'HOME': self.tmp.name, 'XDG_STATE_HOME': self.tmp.name})
        isolation.start()
        self.addCleanup(isolation.stop)
        self.env = {'XDG_STATE_HOME': self.tmp.name, 'QUIPU_SERVER': 'http://example.invalid',
                    'SHANTY_AGENT': 'origin', 'CODEX_THREAD_ID': 'origin-session'}

    def enqueue(self, name):
        return memory.enqueue(dict(entity='handoff', args={'agent': name, 'next': ['next step']}), self.env)

    def test_success_is_fifo_and_append_during_replay_survives(self):
        self.enqueue('first')
        self.enqueue('second')
        seen = []
        def record(entity, args, **kw):
            seen.append(args['agent'])
            if len(seen) == 1:
                self.enqueue('late')  # no network lock can exclude producers
            return memory.Outcome(0, ['recorded'], dict(status='recorded'))
        with mock.patch.object(memory, 'record', side_effect=record):
            out = memory.flush(client=object(), env=self.env, limit=2)
        self.assertEqual(['first', 'second'], seen)
        self.assertEqual(1, out.code)
        self.assertEqual('late', offline._read(memory.queue_path(self.env))[0]['args']['agent'])

    def test_refused_head_stops_replay_and_preserves_tail(self):
        self.enqueue('first')
        self.enqueue('second')
        before = memory.queue_path(self.env).read_bytes()
        with mock.patch.object(memory, 'record', return_value=memory.Outcome(2, ['refused'])) as write:
            out = memory.flush(client=object(), env=self.env)
        self.assertEqual(1, out.code)
        self.assertEqual(1, write.call_count)
        self.assertEqual(before, memory.queue_path(self.env).read_bytes())

    def test_outage_after_first_ack_keeps_only_unconfirmed_suffix(self):
        self.enqueue('first')
        self.enqueue('second')
        with mock.patch.object(memory, 'record', side_effect=[memory.Outcome(0), quipu.Unreachable('offline')]):
            out = memory.flush(client=object(), env=self.env)
        self.assertEqual(1, out.data['replayed'])
        self.assertEqual('second', offline._read(memory.queue_path(self.env))[0]['args']['agent'])

    def test_ack_failure_keeps_replayable_head_instead_of_claiming_success(self):
        self.enqueue('first')
        with mock.patch.object(memory, 'record', return_value=memory.Outcome(0)), \
             mock.patch.object(offline.os, 'replace', side_effect=OSError('disk full')):
            out = memory.flush(client=object(), env=self.env)
        self.assertEqual(0, out.data['replayed'])
        self.assertEqual(1, memory.queue_length(self.env))

    def test_overlapping_replayer_is_silent_and_does_not_touch_records(self):
        self.enqueue('first')
        path = memory.queue_path(self.env)
        with offline.locked(path, replay=True), mock.patch.object(memory, 'record') as record:
            out = memory.flush(client=object(), env=self.env)
        self.assertEqual(1, out.code)
        record.assert_not_called()
        self.assertEqual(1, memory.queue_length(self.env))

    def test_corrupt_or_partial_queue_is_unknown_and_never_overwritten(self):
        self.enqueue('first')
        path = memory.queue_path(self.env)
        for raw in (b'{partial', b'not-json\n'):
            path.write_bytes(raw)
            self.assertIsNone(memory.queue_length(self.env))
            out = memory.flush(client=object(), env=self.env)
            self.assertEqual(1, out.code)
            self.assertEqual(raw, path.read_bytes())
            with self.assertRaises(offline.QueueError):
                self.enqueue('new')
            self.assertEqual(raw, path.read_bytes())

    def test_symlink_queue_is_unknown_and_target_unchanged(self):
        path = memory.queue_path(self.env)
        path.parent.mkdir(parents=True)
        target = Path(self.tmp.name) / 'target'
        target.write_text('keep')
        path.symlink_to(target)
        self.assertIsNone(memory.queue_length(self.env))
        self.assertEqual(1, memory.flush(client=object(), env=self.env).code)
        self.assertEqual('keep', target.read_text())

    def test_concurrent_process_producers_are_not_lost(self):
        # Real separate processes exercise flock; each writer has a unique body.
        code = ('from camayoc_cli.offline import enqueue; import sys; '
                'enqueue({"entity":"handoff","args":{"agent":sys.argv[1]}})')
        env = dict(os.environ, XDG_STATE_HOME=self.tmp.name)
        writers = [subprocess.Popen([sys.executable, '-B', '-c', code, str(i)], env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE) for i in range(6)]
        for writer in writers:
            writer.communicate(timeout=10)
            self.assertEqual(0, writer.returncode)
        self.assertEqual(6, memory.queue_length(self.env))
        self.assertEqual(0o600, memory.queue_path(self.env).stat().st_mode & 0o777)

    def test_origin_is_preserved_without_storing_credentials(self):
        args = {'agent': 'origin', 'next': ['next step']}
        env = dict(self.env, QUIPU_AUTH_TOKEN='never-store-this')
        with mock.patch.object(memory, 'record', side_effect=quipu.Unreachable('offline')):
            out = memory.record_or_queue('handoff', args, env=env)
        self.assertEqual('queued', out.data['status'])
        self.assertNotIn('never-store-this', memory.queue_path(env).read_text())
        replay_env = dict(env, SHANTY_AGENT='flusher', CODEX_THREAD_ID='other')
        with mock.patch.object(memory, 'record', return_value=memory.Outcome(0)) as record:
            memory.flush(client=object(), env=replay_env)
        self.assertEqual('origin', record.call_args.kwargs['env']['SHANTY_AGENT'])
        self.assertEqual('origin-session', record.call_args.kwargs['env']['CODEX_THREAD_ID'])

    def test_different_store_does_not_receive_queued_records(self):
        args = {'agent': 'origin', 'next': ['next step']}
        with mock.patch.object(memory, 'record', side_effect=quipu.Unreachable('offline')):
            memory.record_or_queue('handoff', args, env=self.env)
        with mock.patch.object(memory, 'record') as record:
            out = memory.flush(client=object(), env=dict(self.env, QUIPU_SERVER='http://other.invalid'))
        record.assert_not_called()
        self.assertEqual(1, out.code)
        self.assertEqual(1, memory.queue_length(self.env))

    def test_pending_queue_prevents_a_new_record_leapfrogging_it(self):
        self.enqueue('first')
        with mock.patch.object(memory, 'record') as record:
            out = memory.record_or_queue('handoff', {'agent': 'second', 'next': ['next step']}, env=self.env)
        record.assert_not_called()
        self.assertEqual(2, out.data['queued'])

    def test_flush_hook_always_exits_zero_even_on_buffer_exception(self):
        with mock.patch.object(memory, 'flush_all', side_effect=OSError('no disk')), \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(0, cli.main(['flush', '--hook-worker']))
        with mock.patch.object(memory, 'flush_all', return_value=memory.Outcome(1)) as flush:
            self.assertEqual(0, cli.main(['flush', '--hook-worker']))
        flush.assert_called_once_with(limit=20, timeout=0.75)

    def test_backlog_cli_reports_unknown_and_is_read_only(self):
        with mock.patch.dict(os.environ, self.env), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(0, cli.main(['backlog']))
        self.assertEqual(0, json.loads(output.getvalue())['queued'])
        self.assertFalse(memory.queue_path(self.env).parent.exists())

    def test_backlog_includes_capture_spool_and_unknown_stays_unknown(self):
        self.enqueue('typed')
        spool = memory.queue_path(self.env).with_name('capture-spool.jsonl')
        spool.write_text('{"hash":"capture"}\n')
        self.assertEqual(2, offline.backlog(self.env)['queued'])
        spool.write_text('{partial')
        self.assertIsNone(offline.backlog(self.env)['queued'])

    def test_replay_deadline_prevents_further_http_calls(self):
        client = quipu.Client(deadline=0)
        with mock.patch('urllib.request.urlopen') as request, mock.patch.object(quipu, 'token', return_value=''):
            with self.assertRaises(quipu.TimedOut):
                client.health()
        request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
