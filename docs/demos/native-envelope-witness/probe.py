"""Run on two source revisions using only temporary SQLite fixture custody."""
import importlib.util
import json
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(root / 'scripts'), str(root / 'tests')]
import test_seed_work_item_sync as fixtures

if sys.argv[1:] == ['--compare']:
    import subprocess
    import tempfile
    base = '4b1607c'
    content = subprocess.check_output(['git', 'show', base + ':scripts/seed_work_item_sync.py'], cwd=root)
    with tempfile.NamedTemporaryFile(suffix='.py') as retained:
        retained.write(content)
        retained.flush()
        print('Synthetic fixture: first target succeeds, second control fails.', flush=True)
        print('BEFORE: base ' + base, flush=True)
        subprocess.run([sys.executable, __file__, retained.name], check=True)
        print('AFTER: candidate native-envelope-witness-v1', flush=True)
        subprocess.run([sys.executable, __file__], check=True)
    raise SystemExit(0)

if len(sys.argv) > 1:
    spec = importlib.util.spec_from_file_location('selected_sync', sys.argv[1])
    selected = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(selected)
    fixtures.sync = selected
case = fixtures.SerialTicks()
case.setUp()
original = case.post

def post(endpoint, body):
    if (body.get('query', '').startswith('ASK ') and
            case.item_of(body['query']) == case.ITEMS[1]):
        return {'result': False, 'private': 'PRIVATE-RESPONSE-CANARY'}
    return original(endpoint, body)

case.post = post
try:
    receipt = case.tick()
    visible = {key: receipt[key] for key in
               ('status', 'item', 'attempted_item', 'control', 'requests', 'writes', 'verified')}
    visible['envelope_witness'] = receipt.get('envelope_witness', 'ABSENT')
    print(json.dumps(visible, indent=2, sort_keys=True))
    assert (receipt['status'], receipt['verified'], receipt['writes'], receipt['requests']) == ('UNKNOWN', 1, 2, 8)
    assert 'PRIVATE-RESPONSE-CANARY' not in json.dumps(receipt)
finally:
    case.tearDown()
