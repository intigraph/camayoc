"""Offline capture-anchor comparison; no backend or production state."""
import hashlib
import subprocess
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'tests')]
import directive_edges as candidate
from test_directive_edges import A, NOW, board

BASE = 'f44db6616a58d2291fca096c63f424b3f09e6804'
base = types.ModuleType('baseline_directives')
exec(compile(subprocess.check_output(['git', 'show', BASE + ':scripts/directive_edges.py'],
                                    cwd=ROOT), '<baseline>', 'exec'), base.__dict__)
print('Synthetic graph; capture at 2026-10-07T21:00Z, judged one hour later.')
print('Base:', BASE)
for name in ('blocked_by.py', 'directive_edges.py'):
    print('Candidate', name, hashlib.sha256((ROOT / 'scripts' / name).read_bytes()).hexdigest())
for prefix in ('a:', 'aegis:', A):
    graph = board()

    def query(endpoint, body):
        out = graph(endpoint, body)
        if 'wasGeneratedBy' in body.get('query', ''):
            out['rows'] = [{'e': prefix + 'episode_new'}]
        return out

    old = base.judge(query, 'new', NOW)
    new = candidate.judge(query, 'new', NOW)
    expired = candidate.judge(query, 'new', NOW + candidate.GRACE)
    label = 'full IRI' if prefix == A else prefix
    print(label, 'before:', old['lapse'], old['due_at'],
          'after:', new['lapse'], new['due_at'], 'expired control:', expired['lapse'])
    assert old['lapse'] == ('LAPSED' if prefix == 'a:' else 'NOT_LAPSED')
    assert new['verdict'] == 'UNTRACED' and new['lapse'] == 'NOT_LAPSED'
    assert new['due_at'] == '2026-10-09T21:00:00+00:00'
    assert expired['lapse'] == 'LAPSED'
print('PASS: both compact aliases preserve the capture grace; expiry still lapses.')
