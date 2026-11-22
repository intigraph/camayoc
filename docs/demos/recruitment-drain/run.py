"""Offline replay-retention comparison and fake-controller consumer demo."""
import hashlib
import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
BASE='bd752d0bce33844a67cb6d7f0f15cef164828651'
sys.path.insert(0,str(ROOT/'scripts'))
payload={'repository':{'full_name':'intigraph/widget'},
         'pull_request':{'number':42,'head':{'sha':'a'*40}}}
with tempfile.TemporaryDirectory() as directory:
    for label in ('base','candidate'):
        source=(subprocess.check_output(['git','show',BASE+':scripts/github_ingress.py'],cwd=ROOT)
                if label=='base' else (ROOT/'scripts/github_ingress.py').read_bytes())
        path=Path(directory)/(label+'.py');path.write_bytes(source)
        spec=importlib.util.spec_from_file_location(label,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        spool=module.Spool(':memory:')
        # Same terminal fixture, without claiming a real job ran on either side.
        spool.add('event-1','pull_request',payload,verified_webhook=True,now=1)
        spool.conn.execute("UPDATE recruitment_events SET state='spawned'")
        spool.conn.execute("DELETE FROM deliveries WHERE id='event-1'")
        try:result=spool.add('event-1','pull_request',payload,verified_webhook=True,now=2)
        except Exception as error:
            assert label=='base'
            print('BASE',BASE,'same verified replay after graph retention ->',type(error).__name__)
        else:
            assert label=='candidate' and result=='duplicate'
            print('CANDIDATE ingress SHA256='+hashlib.sha256(source).hexdigest(),'same replay ->',result)
        spool.conn.close()

import github_ingress as gi
from github_recruitment import Candidate
from github_recruitment_drain import drain_once,purge_terminal_bodies,TWO_DAYS
spool=gi.Spool(':memory:');calls=[]
args=dict(repos=frozenset({'intigraph/widget'}),resolve_ready=lambda e,p:Candidate('intigraph/widget',42,'a'*40,'review','task-1'),
          admit=lambda c:None,spawn=lambda c:calls.append(c) or 'fake-controller-receipt',clock=lambda:2.)
for name in ('wake-1','wake-2'):
    spool.add(name,'pull_request',payload,verified_webhook=True,now=1)
    print('FIXTURE consumer',name,drain_once(spool,**args))
assert len(calls)==1
print('FAKE controller calls:',len(calls),'graph rows still pending:',spool.pending())
print('Retention bodies cleared:',purge_terminal_bodies(spool,now=TWO_DAYS+2))
print('Preserved candidate ledger:',spool.conn.execute('SELECT state,receipt FROM recruitment_attempts').fetchall())
print('Preserved tombstones:',spool.conn.execute('SELECT state,length(body) FROM recruitment_events ORDER BY delivery').fetchall())
print('Drain source SHA256='+hashlib.sha256((ROOT/'scripts/github_recruitment_drain.py').read_bytes()).hexdigest())
spool.conn.close()
