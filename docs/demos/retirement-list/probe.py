"""Disposable library demo. No network, tracker or live journal is used."""
import argparse
import hashlib
import inspect
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('scripts',type=Path)
parser.add_argument('--bounded',action='store_true')
args=parser.parse_args()
print('CANDIDATE bounded list' if args.bounded else 'BASE one-slot journal')
sys.path.insert(0,str(args.scripts.resolve()))
import seed_change_custody as custody
import seed_work_items as native
import seed_work_item_sync as sync
import retire_work_item_effect as cli

with tempfile.TemporaryDirectory() as temp:
    board=native.Board('https://example.org/project/demo','demo')
    journal=custody.Journal(Path(temp)/'fixture.sqlite3',board,server='https://example.org',initial_tx=10)
    request={'graph':'urn:demo:metadata','effect':'fixture'}
    digest=hashlib.sha256(json.dumps(request,sort_keys=True).encode()).hexdigest()
    calls=[]
    def absent(endpoint,body):
        assert endpoint=='/query'
        calls.append(endpoint)
        return {'result':False,'truncated':False}
    supported='post' in inspect.signature(cli.operate).parameters
    accepted=0
    for n in range(1,10):
        item=f'demo-{n}'
        envelope={'request':request,'write_id':f'{n:064x}','version':1,'description':'{}'}
        journal.db.execute('INSERT INTO pending(id,tx) VALUES(?,?)',(item,10+n))
        journal.prepare(item,10+n,envelope)
        command=SimpleNamespace(action='retire',item=item,write_id=envelope['write_id'],
            request_sha256=digest,version=1,effect_tx=10+n,reason=f'operator act {n}')
        try:
            result=cli.operate(journal,command,**({'post':absent} if supported else {}))
            accepted+=1
            print(f'operator retire {n}: OK; active={result["retired_effects"]}')
        except custody.CustodyUnknown:
            print(f'operator retire {n}: REFUSED; active={journal.retired_count()}')
    assert accepted==(8 if args.bounded else 1)
    assert len(calls)==(16 if args.bounded else 0)
    print('fresh absence reads:',len(calls))
    writes=[]
    def unknown(endpoint,body,**kwargs):
        assert endpoint!='/knot','no replay is permitted'
        writes.append(endpoint)
        if endpoint=='/knot/status':
            return {'contract':'quipu.knot-completion/v1','write_id':body['write_id'],'state':'unknown'}
        return {'result':False,'truncated':False}
    result=sync.tick(native.Reader(None,board),journal,unknown,changes=None,
        plane='urn:demo:metadata',actor='fixture',completion=True)
    assert result['status']=='UNKNOWN' and result['writes']==0
    print('un-retired UNKNOWN:',result['status'],'writes:',result['writes'])
    if args.bounded:
        journal.release_retired('demo-2',write_id=f'{2:064x}',request_sha256=digest,reason='operator release one')
        assert journal.retired_count()==7
        assert journal.db.execute('SELECT active FROM retired_effect WHERE id=?',('demo-1',)).fetchone()==(1,)
        assert journal.db.execute('SELECT COUNT(*) FROM retired_effect').fetchone()==(8,)
        print('release only entry 2: active=7; preserved records=8; entry 1 remains retired')
        replacement=SimpleNamespace(action='retire',item='demo-9',write_id=f'{9:064x}',
            request_sha256=digest,version=1,effect_tx=19,reason='new explicit operator act after release')
        result=cli.operate(journal,replacement,post=absent)
        assert result['retired_effects']==8
        assert journal.db.execute('SELECT COUNT(*) FROM retired_effect').fetchone()==(9,)
        assert journal.db.execute('SELECT slot FROM retired_effect WHERE id=?',('demo-9',)).fetchone()==(9,)
        print('new independently absent effect after release: OK; active=8; preserved records=9')

    journal.db.close()
print('PASS: fixture only; original pending bodies retained; no network or replay.')
