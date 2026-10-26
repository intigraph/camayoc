"""Synthetic finite-window and fresh-root controls; never a live collector."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import exact_work_item_coverage as coverage
import scheduled_exact_coverage as scheduled


class FiniteCoverage(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.root.chmod(0o700)
        self.home = self.root / 'home'
        self.home.mkdir(mode=0o700)
        self.env = mock.patch.dict(os.environ, HOME=str(self.home))
        self.env.start()
        for name in ('.local/state/camayoc-producer/run.lock', '.local/state/camayoc-workitems/seeds-state.lock'):
            path = self.home / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch(mode=0o600)
        self.now = 1700000000
        self.start, self.end = self.now-60, self.now+300
        self.plan = {'format': 'camayoc-scheduled-coverage-v2',
                     'valid_from': '2023-11-14T22:12:20Z', 'valid_until': '2023-11-14T22:18:20Z',
                     'items': ['demo-canary', 'demo-witness'], 'control': 'demo-canary',
                     'board_graph': 'https://example.invalid/board', 'server': 'https://store.example.invalid',
                     'tag': 'private-fixture',
                     'source_sha256': {name: hashlib.sha256((SCRIPTS/name).read_bytes()).hexdigest()
                                       for name in scheduled.FILES}}
        self.monotonic = 1.0
        self.requests = []

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def save_plan(self, name):
        directory = self.root/name
        directory.mkdir(mode=0o700)
        path = directory/'plan.json'
        path.write_text(json.dumps(self.plan))
        path.chmod(0o600)
        return path, scheduled.digest(path.read_bytes())

    def window(self):
        return coverage.UTCWindow(self.plan['valid_from'], self.plan['valid_until'], clock=lambda: self.now)

    def test_exact_utc_representation_order_and_half_open_boundaries(self):
        for value in (None, '', '2023-11-14T22:12:20+00:00', '2023-11-14T22:12:20.0Z',
                      '2023-11-14 22:12:20Z', '2023-02-30T00:00:00Z'):
            with self.subTest(value=value), self.assertRaises(coverage.Unknown):
                coverage.UTCWindow(value, self.plan['valid_until'])
        with self.assertRaises(coverage.Unknown):
            coverage.UTCWindow(self.plan['valid_until'], self.plan['valid_from'])
        with self.assertRaises(coverage.Unknown):
            coverage.UTCWindow(self.plan['valid_from'], self.plan['valid_from'])
        for now, accepted in ((self.start-1, False), (self.start, True), (self.end-0.001, True),
                              (self.end, False), (self.end+1, False), (None, False),
                              (float('nan'), False), (float('inf'), False), (True, False)):
            with self.subTest(now=now):
                if accepted:
                    self.assertGreater(self.window().remaining(now), 0)
                else:
                    with self.assertRaises(coverage.Unknown):
                        self.window().remaining(now)
        window = self.window()
        window.remaining(self.start+10)
        with self.assertRaises(coverage.Unknown):
            window.remaining(self.start+9)

    def test_expired_not_yet_valid_and_bad_clock_consume_without_collectors_or_child(self):
        for index, now in enumerate((self.start-1, self.end, self.end+1, None, float('nan'))):
            path, sha = self.save_plan('run'+str(index))
            scheduler, child = mock.Mock(side_effect=AssertionError('collector')), mock.Mock(side_effect=AssertionError('child'))
            result = scheduled.execute(self.plan, path, sha, scheduler=scheduler, child=child, clock=lambda: now)
            self.assertEqual(result['status'], 'UNKNOWN')
            self.assertTrue(result['attempt_consumed'])
            self.assertTrue((path.parent/'attempt.json').exists())
            scheduler.assert_not_called(); child.assert_not_called()
            with self.assertRaises(FileExistsError):
                scheduled.execute(self.plan, path, sha, scheduler=scheduler, child=child, clock=lambda: self.now)

    def test_old_or_missing_invalid_bound_plan_refuses_without_upgrade(self):
        original = dict(self.plan)
        for change in ({'format': 'camayoc-scheduled-coverage-v1'}, {'valid_from': None},
                       {'valid_until': '2023-11-14T22:12:20Z'}):
            self.plan = dict(original, **change)
            path, sha = self.save_plan('bad'+str(len(list(self.root.glob('bad*')))))
            before = path.read_bytes()
            with self.assertRaises((scheduled.Stop, coverage.Unknown)):
                scheduled.load_plan(path, sha)
            self.assertEqual(path.read_bytes(), before)
            self.assertFalse((path.parent/'attempt.json').exists())
        self.plan = original

    def test_start_and_last_valid_instant_allow_one_existing_child(self):
        for index, now in enumerate((self.start, self.end-0.001)):
            path, sha = self.save_plan('positive'+str(index))
            scheduler = mock.Mock(return_value={'pid': 40, 'journal_cursor': 'synthetic'})
            child = mock.Mock(return_value={'status': 'COVERED', 'writes': 0})
            result = scheduled.execute(self.plan, path, sha, scheduler=scheduler, child=child, clock=lambda: now)
            self.assertEqual(result['status'], 'COVERED')
            scheduler.assert_called_once(); child.assert_called_once()
            self.assertTrue((path.parent/'attempt.json').exists())

    def test_already_expired_scheduler_invokes_no_reader(self):
        self.now = self.end
        with mock.patch.object(scheduled, 'checked_output', side_effect=AssertionError('late reader')) as reader, \
                mock.patch.object(scheduled, 'proc_identity', side_effect=AssertionError('late proc')) as process:
            with self.assertRaises(scheduled.Stop):
                scheduled.scheduler_receipt('exact', validity=self.window())
            reader.assert_not_called(); process.assert_not_called()

    def test_child_inherits_sealed_bounds_and_remaining_timeout(self):
        self.now = self.end-2
        work = self.root/'child'; work.mkdir(mode=0o700)
        def reply(argv, **kwargs):
            self.assertEqual(argv[argv.index('--valid-from')+1], self.plan['valid_from'])
            self.assertEqual(argv[argv.index('--valid-until')+1], self.plan['valid_until'])
            self.assertLessEqual(kwargs['timeout'], 2)
            kwargs['stdout'].write(json.dumps({'status': 'UNKNOWN', 'writes': 0, 'requests': 0,
                                               'seconds': 0, 'calls': [], 'server': self.plan['server']}).encode())
            return type('Result', (), {'returncode': 2})()
        with mock.patch.object(scheduled.subprocess, 'run', side_effect=reply) as child:
            self.assertEqual(scheduled.child_receipt(self.plan, work, 'synthetic', validity=self.window())['status'], 'UNKNOWN')
            child.assert_called_once()

    def test_expiry_between_scheduler_reads_prevents_next_collector(self):
        for boundary in ('crontab', 'proc', 'journal'):
            self.now = 1700000000
            def read(argv, **kwargs):
                if 'crontab' in argv[0]:
                    if boundary == 'crontab': self.now = self.end
                    return b'exact'
                self.now = self.end
                return b'{}'
            def proc(pid):
                if boundary == 'proc': self.now = self.end
                return {'pid': 40, 'ppid': 1, 'uid': 0, 'comm': 'cron', 'start_ticks': 45}
            with self.subTest(boundary=boundary), mock.patch.object(scheduled, 'checked_output', side_effect=read) as reader, \
                    mock.patch.object(scheduled.os, 'getppid', return_value=40), \
                    mock.patch.object(scheduled, 'proc_identity', side_effect=proc) as process, \
                    mock.patch.object(scheduled, 'verify_scheduler', side_effect=AssertionError('late validation')) as verify:
                with self.assertRaises(scheduled.Stop):
                    scheduled.scheduler_receipt('exact', validity=self.window())
                self.assertEqual(reader.call_count, 2 if boundary == 'journal' else 1)
                self.assertEqual(process.call_count, 0 if boundary == 'crontab' else 1)
                verify.assert_not_called()

    def test_existing_local_reader_timeout_is_clipped_and_late_result_refused(self):
        self.now = self.end-1
        def reply(*args, **kwargs):
            self.assertLessEqual(kwargs['timeout'], 1)
            self.now = self.end
            return type('Result', (), {'returncode': 0, 'stdout': b'exact'})()
        with mock.patch.object(scheduled.subprocess, 'run', side_effect=reply) as reader:
            with self.assertRaises(coverage.Unknown):
                scheduled.checked_output(['synthetic-reader'], validity=self.window())
            reader.assert_called_once()

    def test_expiry_after_scheduler_or_source_prevents_child(self):
        for index, boundary in enumerate(('scheduler', 'source')):
            self.now = 1700000000
            path, sha = self.save_plan('expire'+str(index))
            def scheduler(*_, **kwargs):
                if boundary == 'scheduler': self.now = self.end
                return {'pid': 40, 'journal_cursor': 'synthetic'}
            def source(plan):
                if boundary == 'source': self.now = self.end
                return plan['source_sha256']
            child = mock.Mock(side_effect=AssertionError('late child'))
            with mock.patch.object(scheduled, 'source_receipt', side_effect=source):
                result = scheduled.execute(self.plan, path, sha, scheduler=scheduler, child=child, clock=lambda: self.now)
            self.assertEqual(result['status'], 'UNKNOWN')
            child.assert_not_called()

    def test_child_transport_and_cli_refuse_expiry_before_any_http_or_credential(self):
        window = self.window()
        reader = coverage.Transport('https://store.example.invalid', 'synthetic', validity=window)
        self.now = self.end
        with mock.patch.object(reader.opener, 'open', side_effect=AssertionError('late HTTP')) as opened:
            with self.assertRaises(coverage.Unknown): reader('/query', {'query': 'synthetic'})
            opened.assert_not_called()
        with mock.patch.object(coverage.time, 'time', return_value=self.end), \
                mock.patch.object(coverage.quipu_auth, 'token', side_effect=AssertionError('late credential')) as token, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(coverage.main(['--enable', '--valid-from', self.plan['valid_from'],
                                           '--valid-until', self.plan['valid_until']]), 2)
            self.assertEqual(json.loads(output.getvalue())['status'], 'UNKNOWN')
            token.assert_not_called()

    def test_snapshot_transport_clips_utc_and_retains_only_response_header_allowlist(self):
        reader = coverage.SnapshotTransport(self.plan['server'], 'synthetic', self.window())
        outer = self
        class Response:
            code = 200
            headers = {'Content-Type': 'application/json', 'Authorization': 'PRIVATE-HEADER'}
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, size):
                outer.assertEqual(size, coverage.MAX_BYTES)
                return b'{}'
        payload = coverage.snapshot_payload('board', self.plan['items'], self.plan['board_graph'])
        with mock.patch.object(reader.reader.opener, 'open', return_value=Response()) as opened:
            raw, meta = reader(payload)
            self.assertEqual(raw, b'{}')
            self.assertEqual(opened.call_args.args[0].data, payload)
            self.assertNotIn('PRIVATE', json.dumps(meta))
            self.assertNotIn('Authorization', meta['header_summary'])
            self.now = self.end
            with self.assertRaises(coverage.Unknown): reader(payload)
            self.assertEqual(opened.call_count, 1)

    def test_http_result_arriving_at_expiry_is_unknown_and_no_later_query_opens(self):
        outer = self
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, size):
                outer.now = outer.end
                return b'{}'
        reader = coverage.Transport(self.plan['server'], 'synthetic', validity=self.window())
        with mock.patch.object(reader.opener, 'open', return_value=Response()) as opened:
            for _ in range(2):
                with self.assertRaises(coverage.Unknown): reader('/query', {'query': 'synthetic'})
            self.assertEqual(opened.call_count, 1)

    def rows(self, kind):
        if kind == 'board':
            rows = []
            for item in self.plan['items']:
                for predicate, value in ((coverage.native.SCHEMA+'identifier', item),
                                         (coverage.native.RDF+'type', coverage.native.SCHEMA+'Action'),
                                         (coverage.native.QUECHUA+'sourceKind', 'declared'),
                                         (coverage.native.SCHEMA+'creator', coverage.native.PRINCIPAL+'fixture'),
                                         (coverage.native.SCHEMA+'description', 'PRIVATE-ROW'),
                                         (coverage.legacy.BASE_NS+'version', '1')):
                    rows.append({'w': coverage.native.ITEM+item, 'p': predicate, 'v': value})
            return rows
        item = self.plan['control']
        return [{'w': coverage.legacy.BASE_NS+item, 'p': predicate, 'v': value}
                for predicate, value in ((coverage.legacy.BASE_NS+'identifier', item),
                                         (coverage.native.RDF+'type', coverage.legacy.BASE_NS+'WorkItem'),
                                         (coverage.legacy.BASE_NS+'sourceKind', 'observed'),
                                         (coverage.legacy.BASE_NS+'about', coverage.native.ITEM+item),
                                         ('http://www.w3.org/ns/prov#wasGeneratedBy',
                                          coverage.legacy.BASE_NS+'episode_tracker-work-item_'+item+'_fixture'))]

    def factory(self, seconds):
        self.assertLessEqual(seconds, 6)
        def request(payload):
            self.requests.append(payload)
            self.monotonic += 0.01
            kind = 'records' if coverage.SNAPSHOT_RECORDS in payload.decode() else 'board'
            raw = json.dumps({'truncated': False, 'rows': self.rows(kind)}).encode()
            return raw, {'http_status': 200, 'incomplete': False}
        return request

    def phase(self, root, phase, factory=None):
        return coverage.snapshot_phase(self.plan, 'a'*64, root, phase,
                                       request_factory=factory or self.factory,
                                       clock=lambda: self.monotonic, utc_clock=lambda: self.now)

    def test_fresh_root_four_exact_payloads_private_outputs_and_old_custody_untouched(self):
        old = self.root/'old-custody'; old.mkdir(mode=0o700)
        marker = old/'attempt.json'; marker.write_bytes(b'KEEP-CONSUMED'); marker.chmod(0o600)
        before = (marker.read_bytes(), marker.stat().st_ino, marker.stat().st_mtime_ns)
        root = self.root/'new'
        for phase in ('before', 'after'):
            self.assertEqual(self.phase(root, phase)['status'], 'PASS')
        self.assertEqual(len(self.requests), 4)
        self.assertEqual(self.requests[:2], self.requests[2:])
        self.assertEqual((marker.read_bytes(), marker.stat().st_ino, marker.stat().st_mtime_ns), before)
        self.assertEqual(root.stat().st_mode & 0o777, 0o700)
        for file in root.iterdir(): self.assertEqual(file.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads((root/'full-row-equality.json').read_text()), {'board': True, 'records': True})
        with self.assertRaises(FileExistsError): self.phase(root, 'before')
        with self.assertRaises(FileExistsError): self.phase(root, 'after')
        self.assertEqual(len(self.requests), 4)

    def test_snapshot_default_off_does_not_open_plan_root_transport_or_credentials(self):
        with mock.patch.object(coverage, 'snapshot_phase', side_effect=AssertionError) as snapshot, \
                mock.patch.object(coverage.quipu_auth, 'token', side_effect=AssertionError) as token, \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(coverage.main(['--snapshot-phase', 'before', '--snapshot-root', str(self.root/'off'),
                                           '--manifest', '/synthetic/absent']), 0)
            self.assertEqual(json.loads(out.getvalue()), {'status': 'OFF', 'requests': 0, 'writes': 0})
            snapshot.assert_not_called(); token.assert_not_called()
        self.assertFalse((self.root/'off').exists())

    def test_snapshot_refusal_truncation_overfull_and_slow_reads_retain_failed_checks(self):
        variants = [(b'not-json', {'http_status': 200, 'incomplete': False}, 0.01),
                    (json.dumps({'rows': [], 'truncated': True}).encode(),
                     {'http_status': 200, 'incomplete': False}, 0.01),
                    (json.dumps({'rows': [{}]*257, 'truncated': False}).encode(),
                     {'http_status': 200, 'incomplete': False}, 0.01),
                    (b'x'*coverage.MAX_BYTES, {'http_status': 200, 'incomplete': True}, 0.01),
                    (b'{}', {'http_status': 408, 'incomplete': False}, 0.01),
                    (json.dumps({'rows': self.rows('board'), 'truncated': False}).encode(),
                     {'http_status': 200, 'incomplete': False}, 6.01)]
        for index, (raw, meta, elapsed) in enumerate(variants):
            def factory(seconds):
                def request(payload):
                    self.monotonic += elapsed
                    return raw, meta
                return request
            root = self.root/('invalid'+str(index))
            result = self.phase(root, 'before', factory)
            self.assertEqual((result['status'], result['requests_attempted']), ('UNKNOWN', 1))
            validation = json.loads((root/'before-board-validation.json').read_text())
            self.assertTrue(validation['failed_predicates'])
            self.assertEqual((root/'before-board-response.bin').read_bytes(), raw[:coverage.MAX_BYTES])

    def test_exhausted_snapshot_combined_budget_permits_no_extra_attempt(self):
        root = self.root/'budget'
        self.assertEqual(self.phase(root, 'before')['status'], 'PASS')
        path = root/'before-result.json'
        value = json.loads(path.read_text()); value['seconds'] = 24
        path.write_text(json.dumps(value))
        result = self.phase(root, 'after')
        self.assertEqual((result['status'], result['requests_attempted']), ('UNKNOWN', 0))
        self.assertEqual(len(self.requests), 2)

    def test_snapshot_symlink_root_parent_and_evidence_never_followed(self):
        old = self.root/'old'; old.mkdir(mode=0o700)
        marker = old/'KEEP'; marker.write_bytes(b'private'); marker.chmod(0o600)
        link = self.root/'link'; link.symlink_to(old, target_is_directory=True)
        with self.assertRaises(OSError):
            with coverage.snapshot_directory(link):
                self.fail('followed symlink')
        for root, phase in ((link, 'before'), (link, 'after'), (link/'child', 'before')):
            with self.subTest(root=root, phase=phase), self.assertRaises(OSError): self.phase(root, phase)
        self.assertEqual(list(old.iterdir()), [marker]); self.assertEqual(marker.read_bytes(), b'private')
        root = self.root/'new'; self.assertEqual(self.phase(root, 'before')['status'], 'PASS')
        scope = root/'scope.json'; scope.unlink(); scope.symlink_to(marker)
        with self.assertRaises(OSError): self.phase(root, 'after')
        self.assertEqual(len(self.requests), 2)

    def test_snapshot_failure_is_retained_redacted_and_consumed_without_retry(self):
        def fail(seconds):
            def request(payload): raise ValueError('PRIVATE-ERROR-CREDENTIAL')
            return request
        root = self.root/'failed'
        result = self.phase(root, 'before', fail)
        self.assertEqual((result['status'], result['requests_attempted']), ('UNKNOWN', 1))
        self.assertTrue((root/'before-board-response-meta.json').exists())
        self.assertNotIn('PRIVATE-ERROR', (root/'before-board-response-meta.json').read_text())
        self.assertNotIn('PRIVATE-ERROR', json.dumps(result))
        with self.assertRaises(FileExistsError): self.phase(root, 'before')
        with self.assertRaises(coverage.Unknown): self.phase(root, 'after')

    def test_snapshot_full_equality_keeps_comments_versions_and_identity_failures(self):
        original = self.rows
        for index, change in enumerate(('version', 'description', 'identifier')):
            root = self.root/('compare'+str(index)); self.assertEqual(self.phase(root, 'before')['status'], 'PASS')
            def rows(kind):
                result = original(kind)
                for row in result:
                    if row['p'] == coverage.legacy.BASE_NS+change or row['p'] == coverage.native.SCHEMA+change:
                        row['v'] = 'CHANGED'
                return result
            self.rows = rows
            result = self.phase(root, 'after')
            self.assertEqual(result['status'], 'UNKNOWN')
            self.rows = original

    def test_snapshot_concurrent_after_claims_have_one_winner_and_four_total_attempts(self):
        from concurrent.futures import ThreadPoolExecutor
        root = self.root/'race'
        self.assertEqual(self.phase(root, 'before')['status'], 'PASS')
        def run():
            try:
                return self.phase(root, 'after')['status']
            except FileExistsError:
                return 'REFUSED'
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: run(), range(2)))
        self.assertEqual(sorted(results), ['PASS', 'REFUSED'])
        self.assertEqual(len(self.requests), 4)

    def test_snapshot_expired_root_is_not_created_and_mid_phase_expiry_prevents_second_query(self):
        self.now = self.end
        with self.assertRaises(coverage.Unknown): self.phase(self.root/'expired', 'before')
        self.assertFalse((self.root/'expired').exists())
        self.now = 1700000000
        def factory(seconds):
            request = self.factory(seconds)
            def expire(payload):
                value = request(payload); self.now = self.end; return value
            return expire
        result = self.phase(self.root/'expires', 'before', factory)
        self.assertEqual((result['status'], result['requests_attempted']), ('UNKNOWN', 1))
        self.assertEqual(len(self.requests), 1)


if __name__ == '__main__': unittest.main()
