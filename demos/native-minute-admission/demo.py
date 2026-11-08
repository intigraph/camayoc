#!/usr/bin/env python3
"""Private caller fixtures: minute jitter, physical floor and conservative bootstrap."""
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'tests')]
import test_native_ingress_freshness as tests

BASE = 'a16bad379f532d0f69dccd4d8d699611b354de97'
candidate = tests.sync
cases = [
    ('next-minute-jitter', 1059.999, 1059.999, False, 'BUSY', 'OK'),
    ('forward-wall-step-below-floor', 10000.0, 1029.999, False, 'OK', 'BUSY'),
    ('forward-wall-step-at-floor', 10000.0, 1030.0, False, 'OK', 'OK'),
    ('same-minute-after-floor', 1019.999, 1040.0, False, 'BUSY', 'BUSY'),
    ('backward-wall-step', 900.0, 1060.0, False, 'BUSY', 'BUSY'),
    ('missing-history-bootstrap', 1060.0, 2000.0, True, 'OK', 'BUSY'),
]
with tempfile.TemporaryDirectory() as directory:
    path = Path(directory) / 'before.py'
    path.write_bytes(subprocess.check_output(
        ['git', '-C', str(ROOT), 'show', BASE + ':scripts/sync_work_items.py']))
    spec = importlib.util.spec_from_file_location('native_minute_before', path)
    baseline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline)
    for name, implementation in [('before', baseline), ('candidate', candidate)]:
        tests.sync = implementation
        for label, wall, monotonic, missing, before, after in cases:
            case = tests.NativeBusyDiagnostics()
            case.setUp()
            try:
                if missing:
                    case.admission.unlink()
                result = case.run_native(wall, monotonic=monotonic)
                assert result.output['status'] == (before if name == 'before' else after)
                preserved = (case.state.read_bytes(), case.receipt.read_bytes()) == result.before
                if result.output['status'] == 'BUSY':
                    assert preserved
                    result.tick.assert_not_called()
                    result.push.assert_not_called()
                else:
                    result.tick.assert_called_once()
                print(json.dumps(dict(source=name, case=label, wall=wall, monotonic=monotonic,
                    output=result.output, effect_tick_calls=result.tick.call_count,
                    custody_and_receipt_preserved=preserved), sort_keys=True))
            finally:
                case.doCleanups()
    tests.sync = candidate
print('PASS: jitter admits; physical floor, duplicate/regression and bootstrap controls hold.')
