"""Run the same controlled consumer inputs against base and candidate source."""
import datetime as dt
import hashlib
import subprocess
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT/'scripts'), str(ROOT/'tests')]
import directive_edges as candidate
import directive_deadline as deadline
from test_directive_edges import board, NOW

BASE = '49ba314f1b208ea159a03ed4d641cc18c477721d'
old = types.ModuleType('base_directive_edges')
source = subprocess.check_output(['git','show',BASE+':scripts/directive_edges.py'],cwd=ROOT,text=True)
exec(compile(source, 'base_directive_edges.py', 'exec'), old.__dict__)
print('Private source consumer demonstration; mocked reads, no store or writes.')
print('Base revision:', BASE)
for file in ('directive_edges.py','directive_deadline.py'):
    print('Candidate',file,'SHA256',hashlib.sha256((ROOT/'scripts'/file).read_bytes()).hexdigest())
for label, due, now, expected in [
    ('future explicit date after legacy grace', NOW+dt.timedelta(days=14), NOW+dt.timedelta(days=3), ('LAPSED','NOT_LAPSED')),
    ('past explicit date before legacy grace', NOW-dt.timedelta(days=1), NOW, ('NOT_LAPSED','LAPSED')),
]:
    fake=board()
    def post(endpoint, body):
        if 'traceabilityDueAt' in body.get('query',''):
            return {'rows': [{'due':{'value':due.isoformat(),'datatype':deadline.XSD_DATETIME},'datatype':deadline.XSD_DATETIME}] if body.get('graph') is None else []}
        return fake(endpoint,body)
    result=(old.judge(post,'new',now)['lapse'],candidate.judge(post,'new',now)['lapse'])
    assert result==expected,(label,result)
    print(f'{label}: base={result[0]} candidate={result[1]} PASS')
