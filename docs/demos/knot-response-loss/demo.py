#!/usr/bin/env python3
"""Replay a lost response using private SQLite custody; no network or live writes."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'tests')]
import test_knot_response_resolution as fixture

BASE = '0204212749b3a3730c18a304338e029ebd4c96cd'
candidate = fixture.sync


def run(label, implementation, state='finished'):
    fixture.sync = implementation
    case = fixture.LostResponse()
    case.setUp()
    try:
        case.status_change = lambda value: {**value, 'state': state}
        result = case.tick()
        writes = sum(endpoint == '/knot' for endpoint, _, _ in case.calls)
        summary = dict(source=label, completion_state=state, status=result['status'],
                       writes=writes, verified=result['verified'],
                       pending=case.journal.next() is not None,
                       status_reads=sum(e == '/knot/status' for e, _, _ in case.calls))
        if 'reason' in result:
            summary['reason'] = result['reason']
        print(json.dumps(summary, sort_keys=True), flush=True)
        assert writes == 1
        expected = label == 'candidate' and state == 'finished'
        assert (summary['verified'] == 1) == expected
        assert summary['pending'] != expected
    finally:
        case.doCleanups()
        fixture.sync = candidate


with tempfile.TemporaryDirectory() as directory:
    path = Path(directory) / 'baseline.py'
    path.write_bytes(subprocess.check_output(
        ['git', '-C', str(ROOT), 'show', BASE + ':scripts/seed_work_item_sync.py']))
    spec = importlib.util.spec_from_file_location('response_loss_baseline', path)
    baseline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline)
    print('Synthetic committed write, lost response, original-bound completion and exact effect readback.')
    run('base', baseline)
    run('candidate', candidate)
    run('candidate', candidate, 'unknown')
    print('PASS: finished resolves without resend; UNKNOWN preserves exact pending custody.')
