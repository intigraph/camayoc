"""Replay the same mocked completion response on the base and candidate."""
import inspect
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import types
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts'))
BASE = '49ba314f1b208ea159a03ed4d641cc18c477721d'
for revision in (BASE, 'HEAD'):
    source = subprocess.check_output(
        ['git', 'show', f'{revision}:scripts/seed_work_item_sync.py'], cwd=ROOT, text=True)
    module = types.ModuleType('fixture_sync')
    exec(compile(source, 'fixture_sync.py', 'exec'), module.__dict__)
    print('BASE' if revision == BASE else 'CANDIDATE', subprocess.check_output(
        ['git', 'rev-parse', '--short=8', revision], cwd=ROOT, text=True).strip())
    for state in ('unknown', 'stopped'):
        db = sqlite3.connect(':memory:')
        db.execute('CREATE TABLE verified (id TEXT, version INTEGER)')
        journal = types.SimpleNamespace(db=db)
        envelope = {'write_id': 'a' * 64, 'request': {'graph': 'urn:fixture'}, 'version': 2}
        calls = []
        def post(route, body):
            calls.append(route)
            return {'contract': 'quipu.knot-completion/v1',
                    'write_id': body['write_id'], 'state': state}
        diagnostics = {}
        kwargs = {'diagnostics': diagnostics} if 'diagnostics' in inspect.signature(
            module.completed_without_effect).parameters else {}
        with patch.object(module, 'prove_absent', return_value=False) as proof:
            result = module.completed_without_effect(post, journal, envelope, 'fixture', **kwargs)
        print(json.dumps({'input_state': state, 'release_allowed': result,
                          'calls': calls, 'absence_proofs': proof.call_count,
                          'diagnostics': diagnostics}, sort_keys=True))
        assert result is False and calls == ['/knot/status']
        assert proof.call_count == (1 if state == 'stopped' else 0)
        db.close()
