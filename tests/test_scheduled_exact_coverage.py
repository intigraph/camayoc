"""Private-only schedule admission/fence/cleanup controls; no host state or HTTP."""
import contextlib
import hashlib
import importlib.util
import io
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import scheduled_exact_coverage as subject


class ScheduledCoverage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / 'home'
        self.home.mkdir(mode=0o700)
        self.work = self.root / 'run'
        self.work.mkdir(mode=0o700)
        for name in ('.local/state/camayoc-producer/run.lock',
                     '.local/state/camayoc-workitems/seeds-state.lock'):
            path = self.home / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch(mode=0o600)
        self.home_patch = mock.patch.dict(os.environ, HOME=str(self.home))
        self.home_patch.start()
        self.plan = {'format': 'camayoc-scheduled-coverage-v1',
                     'items': ['demo-canary', 'demo-witness'], 'control': 'demo-canary',
                     'board_graph': 'https://example.invalid/board', 'server': 'https://store.example.invalid',
                     'tag': 'one-proof',
                     'source_sha256': {n: hashlib.sha256((SCRIPTS / n).read_bytes()).hexdigest()
                                       for n in subject.FILES}}
        self.path = self.work / 'plan.json'
        self.save()
        self.scheduler = mock.Mock(return_value={'journal_cursor': 'root-cron', 'pid': 40})
        self.child = mock.Mock(return_value={'status': 'COVERED', 'writes': 0})

    def tearDown(self):
        self.home_patch.stop()
        self.tmp.cleanup()

    def save(self):
        self.path.write_text(json.dumps(self.plan))
        self.path.chmod(0o600)
        self.sha = subject.digest(self.path.read_bytes())

    def run_once(self, **kwargs):
        return subject.execute(self.plan, self.path, self.sha,
                               scheduler=kwargs.get('scheduler', self.scheduler),
                               child=kwargs.get('child', self.child))

    def test_default_off_does_not_touch_plan_locks_source_scheduler_or_child(self):
        with mock.patch.object(subject, 'load_plan', side_effect=AssertionError), \
                mock.patch.object(subject, 'execute', side_effect=AssertionError), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(subject.main([]), 0)
        self.assertEqual(json.loads(out.getvalue()), {'status': 'OFF', 'requests': 0, 'writes': 0})
        self.assertEqual(list(self.work.iterdir()), [self.path])

    def test_plan_exact_two_ids_source_and_private_digest(self):
        self.assertEqual(subject.load_plan(self.path, self.sha), self.plan)
        for change in ({'items': ['demo-canary']}, {'items': ['demo-canary'] * 2},
                       {'control': 'demo-other'}, {'server': 'https://user:secret@bad.invalid'},
                       {'server': 'https://store.example.invalid/path'}, {'source_sha256': {}}, {'tag': 'x\n* *'}):
            original = self.plan.copy()
            self.plan.update(change)
            self.save()
            with self.assertRaises((ValueError, TypeError)):
                subject.load_plan(self.path, self.sha)
            self.plan = original
        self.save()
        with self.assertRaises(subject.Stop):
            subject.load_plan(self.path, '0' * 64)
        self.path.chmod(0o644)
        with self.assertRaises(subject.Stop):
            subject.load_plan(self.path, self.sha)

    def test_regular_plan_passes_fifo_refuses_without_wait_for_loader_and_cleanup(self):
        loader = [sys.executable, '-c',
                  "import sys;sys.path.insert(0,sys.argv[1]);import scheduled_exact_coverage as s;"
                  "s.load_plan(s.Path(sys.argv[2]),sys.argv[3])",
                  str(SCRIPTS), str(self.path), self.sha]
        filter_cmd = [sys.executable, str(SCRIPTS / 'scheduled_exact_coverage.py'),
                      '--cleanup-filter', '--manifest', str(self.path),
                      '--manifest-sha256', self.sha]
        exact = subject.entry(self.path, self.sha, self.plan)
        for command in (loader, filter_cmd):
            self.save()
            regular = subprocess.run(command, input=(exact + '\n').encode(),
                                     capture_output=True, timeout=1.5)
            self.assertEqual(regular.returncode, 0, regular.stderr)
            self.path.unlink()
            os.mkfifo(self.path, 0o600)
            fifo = subprocess.run(command, input=(exact + '\n').encode(),
                                  capture_output=True, timeout=1.5)
            self.assertNotEqual(fifo.returncode, 0)
            self.assertEqual(fifo.stdout, b'')
            self.assertIn(b'private plan required', fifo.stderr)
            self.assertFalse((self.work / 'attempt.json').exists())
            self.assertFalse((self.work / 'stdout').exists())
            self.path.unlink()
        self.save()

    def test_wrong_source_no_fence_or_child(self):
        self.plan['source_sha256'][subject.FILES[0]] = '0' * 64
        with self.assertRaises(subject.Stop):
            self.run_once()
        self.assertFalse((self.work / 'attempt.json').exists())
        self.child.assert_not_called()

    def test_admitted_positive_consumes_once_before_child(self):
        def child(*_, **kwargs):
            self.assertEqual(len(kwargs['lock_fds']), 2)
            self.assertEqual(json.loads((self.work / 'attempt.json').read_text())['state'],
                             'consumed-before-child')
            return {'status': 'COVERED', 'writes': 0}
        self.assertEqual(self.run_once(child=child)['status'], 'COVERED')
        with self.assertRaises(FileExistsError):
            self.run_once()
        self.child.assert_not_called()

    def test_timeout_unknown_consumes_no_retry(self):
        self.child.side_effect = subprocess.TimeoutExpired('private-child', 73)
        self.assertEqual(self.run_once()['status'], 'UNKNOWN')
        with self.assertRaises(FileExistsError):
            self.run_once()
        self.assertEqual(self.child.call_count, 1)

    def test_uncertain_receipt_consumes_attempt(self):
        self.child.side_effect = ValueError('malformed/private details')
        result = self.run_once()
        self.assertEqual(result['error'], 'ValueError')
        self.assertNotIn('private details', json.dumps(result))
        self.assertTrue((self.work / 'attempt.json').exists())

    def test_hold_consumes_without_child_and_cannot_retry(self):
        (self.home / '.aegis-hold').touch()
        self.assertEqual(self.run_once()['status'], 'UNKNOWN')
        (self.home / '.aegis-hold').unlink()
        with self.assertRaises(FileExistsError):
            self.run_once()
        self.child.assert_not_called()

    def test_busy_consumes_without_child_and_later_tick_cannot_retry(self):
        with subject.locked([self.home / '.local/state/camayoc-producer/run.lock']):
            result = self.run_once()
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertIn('busy', result['reason'])
        with self.assertRaises(FileExistsError):
            self.run_once()
        self.child.assert_not_called()

    def test_symlink_lock_consumes_without_child(self):
        lock = self.home / '.local/state/camayoc-producer/run.lock'
        lock.unlink()
        lock.symlink_to(self.path)
        self.assertEqual(self.run_once()['status'], 'UNKNOWN')
        self.child.assert_not_called()
        with self.assertRaises(FileExistsError):
            self.run_once()

    def test_scheduler_unknown_consumes_no_child_no_retry(self):
        self.scheduler.side_effect = subject.Stop('no independent root receipt')
        self.assertEqual(self.run_once()['status'], 'UNKNOWN')
        self.assertTrue((self.work / 'attempt.json').exists())
        self.child.assert_not_called()
        with self.assertRaises(FileExistsError):
            self.run_once()

    def test_proc_ancestry_does_not_require_ptrace_gated_root_exe(self):
        proc = self.root / 'proc'
        process = proc / '40'
        process.mkdir(parents=True)
        fields = ['0'] * 20
        fields[0], fields[1], fields[19] = 'S', '1', '45'
        (process / 'stat').write_text('40 (cron) ' + ' '.join(fields))
        (process / 'comm').write_text('cron\n')
        with mock.patch.object(subject.os, 'readlink', side_effect=PermissionError) as denied:
            value = subject.proc_identity(40, proc_root=proc)
        denied.assert_not_called()
        self.assertEqual(value['comm'], 'cron')
        self.assertEqual(value['start_ticks'], 45)
        self.assertEqual(value['ppid'], 1)

    def test_scheduler_boundary_requires_exact_root_start_and_live_ancestry(self):
        exact = subject.entry(self.path, self.sha, self.plan)
        import pwd
        user = pwd.getpwuid(os.getuid()).pw_name
        record = {'_UID': '0', '_EXE': '/usr/sbin/cron', '_SYSTEMD_UNIT': 'cron.service',
                  '_PID': '40', '__CURSOR': 'independent', '__REALTIME_TIMESTAMP': '1000000000',
                  'MESSAGE': '(' + user + ') CMD (' + exact.split(' ', 5)[5] + ')'}
        ancestors = [{'pid': 40, 'uid': 0, 'comm': 'cron', 'start_ticks': 45}]
        self.assertEqual(subject.verify_scheduler(exact, exact + '\n', ancestors, [record], 1001)['pid'], 40)
        for change in ({'_UID': '100001'}, {'_EXE': '/tmp/cron'}, {'_PID': '41'},
                       {'_SYSTEMD_UNIT': 'user.service'}, {'MESSAGE': 'caller-run-id'},
                       {'__REALTIME_TIMESTAMP': '1000000'}):
            with self.assertRaises(subject.Stop):
                subject.verify_scheduler(exact, exact, ancestors, [dict(record, **change)], 1001)
        for current in ('', exact + '\n' + exact):
            with self.assertRaises(subject.Stop):
                subject.verify_scheduler(exact, current, ancestors, [record], 1001)
        with self.assertRaises(subject.Stop):
            subject.verify_scheduler(exact, exact, [], [record], 1001)
        for fake in ([dict(ancestors[0], uid=100001)],
                     [dict(ancestors[0], comm='shell')]):
            with self.assertRaises(subject.Stop):
                subject.verify_scheduler(exact, exact, fake, [record], 1001)

    def test_real_child_receipt_boundary_rejects_errors_and_caps(self):
        good = {'status': 'COVERED', 'writes': 0, 'requests': 15, 'seconds': 1.0,
                'server': self.plan['server'],
                'items': sorted(self.plan['items']), 'control': self.plan['control'],
                'covered': sorted(self.plan['items']), 'missing': [],
                'source_sha256': {k: v for k, v in self.plan['source_sha256'].items()
                                  if k != 'scheduled_exact_coverage.py'},
                'calls': [{'seconds': 0.01, 'response_bytes': 50} for _ in range(15)]}
        variants = [({}, False), ({'requests': 16}, True), ({'writes': 1}, True),
                    ({'seconds': 71}, True), ({'items': ['demo-other']}, True),
                    ({'source_sha256': {}}, True), ({'server': 'https://other.invalid'}, True),
                    ({'covered': []}, True),
                    ({'calls': [{'seconds': 7, 'response_bytes': 50}] * 15}, True),
                    ({'calls': [{'seconds': 0, 'response_bytes': 262145}] * 15}, True),
                    ({'calls': []}, True)]
        for num, (change, refused) in enumerate(variants):
            work = self.work / str(num)
            work.mkdir(mode=0o700)
            value = dict(good, **change)
            def emit(*args, **kwargs):
                self.assertEqual(args[0][1], str(SCRIPTS / 'exact_work_item_coverage.py'))
                self.assertNotIn('--initialise', args[0])
                self.assertEqual(kwargs['env']['QUIPU_SERVER'], self.plan['server'])
                kwargs['stdout'].write(json.dumps(value).encode())
                return subprocess.CompletedProcess(args[0], 0)
            with mock.patch.object(subject.subprocess, 'run', side_effect=emit):
                if refused:
                    with self.assertRaises(subject.Stop):
                        subject.child_receipt(self.plan, work, 'test-run')
                else:
                    self.assertEqual(subject.child_receipt(self.plan, work, 'test-run')['status'], 'COVERED')

    def test_cleanup_filter_failure_has_no_partial_stdout(self):
        with mock.patch('sys.stdin', io.StringIO('foreign only\n')), \
                contextlib.redirect_stdout(io.StringIO()) as out, \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(subject.main(['--cleanup-filter', '--manifest', str(self.path),
                                           '--manifest-sha256', self.sha]), 2)
        self.assertEqual(out.getvalue(), '')

    def test_cron_paths_reject_percent_and_newline(self):
        for name in ('bad%path', 'bad\npath'):
            with self.assertRaises(subject.Stop):
                subject.entry(self.work / name, self.sha, self.plan)

    def test_cleanup_cli_preserves_crlf_and_newer_entry(self):
        exact = subject.entry(self.path, self.sha, self.plan)
        current = b'# foreign KEEP\r\n' + exact.encode() + b'\n# newer owner\r\n'
        result = subprocess.run([sys.executable, str(SCRIPTS / 'scheduled_exact_coverage.py'),
                                 '--cleanup-filter', '--manifest', str(self.path),
                                 '--manifest-sha256', self.sha], input=current, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b'# foreign KEEP\r\n# newer owner\r\n')

    def test_cleanup_preserves_concurrent_unrelated_bytes(self):
        exact = subject.entry(self.path, self.sha, self.plan)
        old = '# foreign KEEP\r\n* * * * * other\n' + exact + '\n# newer owner\n'
        self.assertEqual(subject.cleanup(old, exact), '# foreign KEEP\r\n* * * * * other\n# newer owner\n')
        for text in ('', exact + '\n' + exact + '\n', exact + ' extra\n'):
            with self.assertRaises(subject.Stop):
                subject.cleanup(text, exact)

    def test_crash_before_child_consumes_attempt(self):
        original = subject.persist_new
        def crash():
            def die(path, value):
                original(path, value)
                os._exit(19)
            with mock.patch.object(subject, 'persist_new', side_effect=die):
                self.run_once()
        ctx = multiprocessing.get_context('fork')
        p = ctx.Process(target=crash)
        p.start(); p.join(5)
        self.assertEqual(p.exitcode, 19)
        with self.assertRaises(FileExistsError):
            self.run_once()
        self.child.assert_not_called()

    def test_crash_after_child_no_reinvocation(self):
        def crash_child(*_, **kwargs):
            (self.work / 'child-entered').touch()
            os._exit(20)
        p = multiprocessing.get_context('fork').Process(target=lambda: self.run_once(child=crash_child))
        p.start(); p.join(5)
        self.assertEqual(p.exitcode, 20)
        self.assertTrue((self.work / 'child-entered').exists())
        with self.assertRaises(FileExistsError):
            self.run_once()
        self.child.assert_not_called()

    def test_concurrent_claims_have_one_winner(self):
        # Even without the outer locks, the durable attempt claim is exclusive.
        gate = multiprocessing.get_context('fork').Barrier(2)
        def claim():
            gate.wait(timeout=5)
            try:
                subject.persist_new(self.work / 'attempt.json', {'claimed': True})
            except FileExistsError:
                os._exit(3)
        workers = [multiprocessing.get_context('fork').Process(target=claim) for _ in range(2)]
        for p in workers: p.start()
        for p in workers: p.join(5)
        self.assertEqual(sorted(p.exitcode for p in workers), [0, 3])

    def test_partial_fence_is_never_repaired_or_reset(self):
        (self.work / 'attempt.json').write_text('{')
        with self.assertRaises(FileExistsError):
            self.run_once()
        self.assertEqual((self.work / 'attempt.json').read_text(), '{')
        self.child.assert_not_called()


if __name__ == '__main__':
    unittest.main()
