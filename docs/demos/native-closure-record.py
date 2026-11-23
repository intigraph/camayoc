"""One private closure fixture on a base revision and the current source tree."""
import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests'))
from test_seed_work_items import rows, ID

BASE = 'e86c5da25585cb90a68989bb68cdefcf758841e2'
STAMP = '2026-10-10T12:00:00.123456789Z'
PREDICATE = 'https://schema.org/endTime'


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def run(module, label):
    calls = []
    data = rows(**{module.SEEDS+'status': 'closed', PREDICATE: STAMP,
                   module.SCHEMA+'result': 'Completed private fixture'})
    def post(endpoint, body):
        calls.append(body)
        return {'rows': [row for row in data if row['p'] in module.PROPERTIES],
                'truncated': False}
    reader = module.Reader(post, module.Board('urn:private:board', 'demo'))
    record = reader.get(ID)
    projection = (record.closure_record() if hasattr(record, 'closure_record')
                  else record.tracker_record())
    print(label, 'closed_at=', projection.get('closed_at'),
          'reason=', projection.get('close_reason'), 'queries=', len(calls))
    return projection


print('Private exact-record fixture; no server, stream or producer is started.')
print('Input close time:', STAMP)
with tempfile.TemporaryDirectory() as directory:
    base = Path(directory) / 'base.py'
    base.write_bytes(subprocess.check_output(
        ['git', 'show', BASE+':scripts/seed_work_items.py'], cwd=ROOT))
    before = run(load(base, 'closure_demo_base'), 'BASE '+BASE[:8])
    assert before.get('closed_at') is None
after_module = load(ROOT / 'scripts/seed_work_items.py', 'closure_demo_head')
after = run(after_module, 'HEAD')
assert after['closed_at'] == STAMP
assert after['close_reason'] == 'Completed private fixture'
missing = rows(**{after_module.SEEDS+'status': 'closed'})
reader = after_module.Reader(lambda *a: {'rows': missing, 'truncated': False},
                             after_module.Board('urn:private:board', 'demo'))
try:
    reader.get(ID).closure_record()
except after_module.NativeUnknown:
    print('Missing close time: UNKNOWN; modification time is never substituted.')
else:
    raise AssertionError('missing closure time accepted')
