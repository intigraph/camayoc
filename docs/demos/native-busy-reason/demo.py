#!/usr/bin/env python3
"""Replay both native BUSY decisions with private files and no service calls."""
import fcntl
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'tests')]
import test_native_ingress_freshness as tests

BASE = '871d9a95bf288feac3256ee57ad5dd83fede15da'
candidate = tests.sync
with tempfile.TemporaryDirectory() as directory:
    path = Path(directory) / 'before.py'
    path.write_bytes(subprocess.check_output(
        ['git', '-C', str(ROOT), 'show', BASE + ':scripts/sync_work_items.py']))
    spec = importlib.util.spec_from_file_location('native_busy_before', path)
    baseline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline)
    after_path = Path(directory) / 'after.py'
    after_path.write_bytes(subprocess.check_output(
        ['git', '-C', str(ROOT), 'show',
         'a16bad379f532d0f69dccd4d8d699611b354de97:scripts/sync_work_items.py']))
    spec = importlib.util.spec_from_file_location('native_busy_after', after_path)
    after = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(after)
    for name, implementation in [('before', baseline), ('candidate', after)]:
        tests.sync = implementation
        for reason in ('lock-held', 'interval-not-due'):
            case = tests.NativeBusyDiagnostics()
            case.setUp()
            try:
                if reason == 'lock-held':
                    with case.state.with_suffix('.lock').open('a') as held:
                        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        result = case.run_native(1061.0)
                else:
                    result = case.run_native(1059.999)
                assert result.code == 0 and result.output['status'] == 'BUSY'
                assert result.output.get('busy_reason') == (reason if name == 'candidate' else None)
                assert (case.state.read_bytes(), case.receipt.read_bytes()) == result.before
                result.tick.assert_not_called()
                result.push.assert_not_called()
                print(json.dumps(dict(source=name, case=reason, output=result.output), sort_keys=True))
            finally:
                case.doCleanups()
    tests.sync = candidate
print('PASS: reasons separate; custody, receipt, zero work and freshness publication unchanged.')
