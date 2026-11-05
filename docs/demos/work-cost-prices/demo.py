#!/usr/bin/env python3
"""Local request fixtures only; no provider or graph calls."""
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
ROOT = Path(__file__).resolve().parents[3]
BASE = '63f200d18671179827a05fd6b717a23f4a8e078c'
with TemporaryDirectory() as directory:
    temporary = Path(directory)
    old = temporary / 'old'; old.mkdir()
    for name in ('work_cost.py', 'ingest_session_usage.py'):
        (old/name).write_bytes(subprocess.check_output(['git', 'show', BASE+':scripts/'+name], cwd=ROOT))
    rows = []
    for key, model in [('priced', 'claude-opus-5-5'), ('unknown', 'unpublished-model')]:
        rows.append({'sessionId': 'fixture', 'requestId': key, 'timestamp': 5,
                     'message': {'model': model, 'usage': {
                         'input_tokens': 1000, 'output_tokens': 50,
                         'cache_read_input_tokens': 2000, 'cache_creation_input_tokens': 500,
                         'cache_creation': {'ephemeral_5m_input_tokens': 200, 'ephemeral_1h_input_tokens': 300},
                         'speed': 'standard', 'service_tier': 'standard', 'inference_geo': 'global'}}})
    source = temporary / 'requests.jsonl'
    source.write_text(''.join(json.dumps(row)+'\n' for row in rows))
    params = {'sources': [{'path': str(source), 'harness': 'claude', 'agent': 'worker'}],
              'bindings': [{'schema_version': 1, 'rig': 'example', 'bead': 'task-a',
                            'agent': 'worker', 'session': 'fixture', 'start': 0, 'end': 10}]}
    params_path = temporary / 'params.json'; params_path.write_text(json.dumps(params))
    command = "import json,sys;sys.path.insert(0,sys.argv[1]);import work_cost;r=work_cost.retrieve(json.load(open(sys.argv[2])));print(json.dumps({'tokens':sum(g['tokens'] for g in r['groups']),'costs':r.get('costs')},sort_keys=True))"
    for label, location in [('BASE '+BASE, old), ('CANDIDATE pricing source', ROOT/'scripts')]:
        output = subprocess.check_output([sys.executable, '-c', command, str(location), str(params_path)], text=True)
        result = json.loads(output)
        print(label)
        if result['costs'] is None:
            print(json.dumps(result, sort_keys=True)); assert result['tokens'] == 7100
        else:
            cost = result['costs']
            print(json.dumps({'tokens': result['tokens'], 'usd': cost['usd'], 'known_usd': cost['known_usd'],
                              'priced_tokens': cost['priced_tokens'], 'unpriced_models': cost['unpriced_models'],
                              'plan_share_usd': cost['plan_share_usd']}, sort_keys=True))
            assert cost['usd'] is None and cost['known_usd'] == '0.0088'
            assert cost['priced_tokens'] == 3550 and cost['unpriced_models'] == ['unpublished-model']
print('PASS: same measured tokens; partial prices remain explicitly incomplete')
