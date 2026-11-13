"""Real durable custody and normal delivery: no fake receiver completion."""
import hashlib
import io
import fcntl
import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import seed_change_custody as custody
import seed_work_item_sync as sync
import seed_work_items as native
import retire_work_item_effect as cli
import sync_work_items as scheduled
from test_seed_work_items import GRAPH,ID,rows
from test_seed_change_custody import page


class Retirement(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'journal.sqlite3'
        self.board=native.Board(GRAPH,'demo');self.server='https://peer.example.test'
        self.j=custody.Journal(self.path,self.board,server=self.server,initial_tx=10)
        self.env={'request':{'graph':'urn:private:knowledge','exact':'original'},
                  'write_id':'a'*64,'version':1,'description':'{}'}
        self.j.db.execute('INSERT INTO pending(id,tx) VALUES(?,?)',(ID,11))
        self.j.prepare(ID,11,self.env);self.original=self.j.next()[2]
        self.digest=hashlib.sha256(json.dumps(self.env['request'],sort_keys=True).encode()).hexdigest()
    def tearDown(self):self.j.db.close();self.temp.cleanup()
    def retire(self,**overrides):
        args=dict(write_id='a'*64,request_sha256=self.digest,version=1,effect_tx=11,reason='operator task:test',now=100,prove_absent=lambda item,envelope:True)
        args.update(overrides);self.j.retire_effect(ID,**args)
    def test_exact_envelope_survives_restart_and_newer_candidates_stay_excluded(self):
        self.retire();self.j.retain(page(99))
        self.j.db.close();self.j=custody.Journal(self.path,self.board,server=self.server)
        self.assertIsNone(self.j.next());self.assertEqual(self.j.backlog_count(),0)
        self.assertEqual(self.j.retired_count(),1)
        frozen=self.j.db.execute('SELECT body,tx,effect_tx FROM retired_effect').fetchone()
        self.assertEqual(frozen,(self.original,11,11))
        self.assertEqual(self.j.db.execute('SELECT body,tx,effect_tx FROM pending').fetchone(),(self.original,99,11))
        for action in (lambda:self.j.release_unconfirmed(ID,self.original),lambda:self.j.acknowledge(ID,11,1),
                       lambda:self.j.prepare(ID,99,self.env)):
            with self.assertRaises(custody.CustodyUnknown):action()
    def test_binding_drift_refuses_atomically(self):
        for delta in ({'write_id':'b'*64},{'request_sha256':'0'*64},{'version':2},{'version':True},
                      {'effect_tx':12},{'reason':''},{'now':float('nan')}):
            with self.subTest(delta=delta),self.assertRaises(custody.CustodyUnknown):self.retire(**delta)
            self.assertEqual(self.j.retired_count(),0);self.assertEqual(self.j.next()[2],self.original)
        self.j.db.execute('UPDATE pending SET body=?',(self.original.replace('original','changed'),))
        with self.assertRaises(custody.CustodyUnknown):self.retire()
    def test_duplicate_history_and_foreign_journal_refused(self):
        self.retire()
        with self.assertRaises(custody.CustodyUnknown):self.retire()
        with self.assertRaises(custody.CustodyUnknown):
            custody.Journal(self.path,self.board,server='https://other.example.test')
    def add_effect(self,n):
        item=f'demo-retired-{n}'
        env={**self.env,'write_id':f'{n:064x}'}
        self.j.db.execute('INSERT INTO pending(id,tx) VALUES(?,?)',(item,20+n))
        self.j.prepare(item,20+n,env)
        return item,dict(write_id=env['write_id'],request_sha256=self.digest,version=1,
                         effect_tx=20+n,reason=f'operator act {n}',now=100+n,
                         prove_absent=lambda item,envelope:True)
    def test_second_retirement_and_ninth_refusal_keep_each_exact_effect(self):
        self.retire()
        entries=[]
        for n in range(1,8):
            item,binding=self.add_effect(n);entries.append((item,binding))
            self.j.retire_effect(item,**binding)
        self.assertEqual(self.j.retired_count(),8)
        before=list(self.j.db.execute('SELECT * FROM retired_effect ORDER BY slot'))
        item,binding=self.add_effect(8)
        with self.assertRaises(custody.CustodyUnknown):self.j.retire_effect(item,**binding)
        self.assertEqual(list(self.j.db.execute('SELECT * FROM retired_effect ORDER BY slot')),before)
        # Releasing one entry frees active capacity without deleting audit history.
        other,other_binding=entries[0]
        self.j.release_retired(other,write_id=other_binding['write_id'],
            request_sha256=self.digest,reason='operator release one')
        self.assertEqual(self.j.retired_count(),7)
        self.assertEqual(self.j.db.execute('SELECT active FROM retired_effect WHERE id=?',(ID,)).fetchone(),(1,))
        self.j.retire_effect(item,**binding)
        self.assertEqual(self.j.retired_count(),8)
        self.assertEqual(self.j.db.execute('SELECT COUNT(*) FROM retired_effect').fetchone(),(9,))
        self.assertEqual(self.j.db.execute('SELECT slot FROM retired_effect WHERE id=?',(item,)).fetchone(),(9,))
        self.assertEqual(list(self.j.db.execute('SELECT * FROM retired_effect WHERE slot!=2 AND slot<=8 ORDER BY slot')), [r for r in before if r[0]!=2])
        another,another_binding=self.add_effect(9)
        with self.assertRaises(custody.CustodyUnknown):self.j.retire_effect(another,**another_binding)
    def test_absence_is_fresh_inside_cas_and_present_or_unknown_refuses(self):
        before=list(self.j.db.iterdump())
        for result in (False,None,{},1):
            calls=[]
            def proof(item,envelope):
                calls.append((item,envelope['write_id'],self.j.db.in_transaction))
                return result
            with self.assertRaises(custody.CustodyUnknown):self.retire(prove_absent=proof)
            self.assertEqual(calls,[(ID,'a'*64,True)])
            self.assertEqual(list(self.j.db.iterdump()),before)
        calls=[]
        def proof(item,envelope):
            calls.append(self.j.db.in_transaction);return True
        self.retire(prove_absent=proof)
        self.assertEqual(calls,[True])
    def test_existing_one_slot_journal_migrates_without_losing_history(self):
        self.retire()
        before=self.j.db.execute('SELECT * FROM retired_effect').fetchall()
        schema=self.j.db.execute("SELECT sql FROM sqlite_master WHERE name='retired_effect'").fetchone()[0]
        self.j.db.execute('ALTER TABLE retired_effect RENAME TO retired_effect_new')
        self.j.db.execute(schema.replace('>=1','=1'))
        self.j.db.execute('INSERT INTO retired_effect SELECT * FROM retired_effect_new')
        self.j.db.execute('DROP TABLE retired_effect_new')
        self.j.db.close();self.j=custody.Journal(self.path,self.board,server=self.server)
        self.assertEqual(self.j.db.execute('SELECT * FROM retired_effect').fetchall(),before)
        item,binding=self.add_effect(1);self.j.retire_effect(item,**binding)
        self.assertEqual(self.j.retired_count(),2)
    def test_lifetime_schema_migration_and_bounded_history_pages(self):
        self.retire()
        old=self.j.db.execute('SELECT * FROM retired_effect').fetchall()
        schema=self.j.db.execute("SELECT sql FROM sqlite_master WHERE name='retired_effect'").fetchone()[0]
        self.j.db.execute('ALTER TABLE retired_effect RENAME TO retired_effect_old')
        self.j.db.execute(schema.replace('>=1',' BETWEEN 1 AND 8'))
        self.j.db.execute('INSERT INTO retired_effect SELECT * FROM retired_effect_old')
        self.j.db.execute('DROP TABLE retired_effect_old')
        self.j.db.close();self.j=custody.Journal(self.path,self.board,server=self.server)
        self.assertEqual(self.j.db.execute('SELECT * FROM retired_effect').fetchall(),old)
        for n in range(1,40):
            item,binding=self.add_effect(n);self.j.retire_effect(item,**binding)
            self.j.release_retired(item,write_id=binding['write_id'],request_sha256=self.digest,reason='release')
        first=cli.operate(self.j,SimpleNamespace(action='inspect',history_limit=32,after_slot=0))
        self.assertEqual(len(first['records']),32);self.assertTrue(first['history_has_more'])
        second=cli.operate(self.j,SimpleNamespace(action='inspect',history_limit=32,after_slot=first['history_next_after']))
        self.assertEqual(len(second['records']),8);self.assertFalse(second['history_has_more'])
        self.assertEqual([r['slot'] for r in first['records']+second['records']],list(range(1,41)))
        self.assertEqual([r['id'] for r in second['active_records']],[ID])
        before=list(self.j.db.iterdump())
        for limit,after in ((0,0),(65,0),(1,-1)):
            with self.assertRaises(custody.CustodyUnknown):
                cli.operate(self.j,SimpleNamespace(action='inspect',history_limit=limit,after_slot=after))
        self.assertEqual(list(self.j.db.iterdump()),before)
    def test_cli_requires_two_absence_reads_and_present_subject_is_refused(self):
        args=SimpleNamespace(action='retire',item=ID,write_id='a'*64,request_sha256=self.digest,
                             version=1,effect_tx=11,reason='one explicit operator act')
        before=list(self.j.db.iterdump())
        for response in ({'result':True,'truncated':False},{'result':False,'truncated':True},{'result':False},{'result':False,'truncated':False,'padding':'x'*4096},{},None,
                         {'labels':None,'result':True}, {'labels':None,'result':'false'},
                         {'labels':{},'result':False}, {'labels':None,'result':False,'rows':[]},
                         {'labels':None,'result':False,'truncated':True},
                         {'labels':None,'result':False,'count':0}):
            with self.assertRaises(custody.CustodyUnknown):cli.operate(self.j,args,post=lambda *a:response)
            self.assertEqual(list(self.j.db.iterdump()),before)
        calls=[]
        def post(endpoint,body):
            calls.append((endpoint,body));return {'result':False,'truncated':False}
        result=cli.operate(self.j,args,post=post)
        self.assertEqual(result['retired_effects'],1)
        self.assertEqual(len(calls),2)
        self.assertTrue(all(endpoint=='/query' for endpoint,body in calls))
        self.assertIn('GRAPH ?g',calls[0][1]['query'])
        self.assertNotIn('GRAPH',calls[1][1]['query'])
    def test_cli_canonical_ask_without_truncation_metadata(self):
        # Exact original-client response from Quipu's ASK branch. SELECT-only
        # truncation metadata is absent; two independent false reads are needed.
        args=SimpleNamespace(action='retire',item=ID,write_id='a'*64,request_sha256=self.digest,
                             version=1,effect_tx=11,reason='one explicit operator act')
        calls=[]
        def post(endpoint,body):
            calls.append((endpoint,body));return json.loads('{"labels":null,"result":false}')
        result=cli.operate(self.j,args,post=post)
        self.assertEqual(result['retired_effects'],1)
        self.assertEqual(len(calls),2)
        self.assertIn('GRAPH ?g',calls[0][1]['query'])
        self.assertNotIn('GRAPH',calls[1][1]['query'])
        self.assertEqual(self.j.db.execute('SELECT body FROM pending').fetchone()[0],self.original)
        self.assertEqual(self.j.db.execute('SELECT body FROM retired_effect').fetchone()[0],self.original)
    def test_cli_second_ask_must_also_prove_absence(self):
        args=SimpleNamespace(action='retire',item=ID,write_id='a'*64,request_sha256=self.digest,
                             version=1,effect_tx=11,reason='one explicit operator act')
        before=list(self.j.db.iterdump())
        for second in ({'labels':None,'result':True}, {'result':False},
                       {'labels':None,'result':False,'truncated':True},
                       {'labels':None,'result':False,'rows':[]}):
            responses=iter([{'labels':None,'result':False},second]);calls=[]
            def post(endpoint,body):
                calls.append(body);return next(responses)
            with self.subTest(second=second),self.assertRaises(custody.CustodyUnknown):
                cli.operate(self.j,args,post=post)
            self.assertEqual(len(calls),2)
            self.assertEqual(list(self.j.db.iterdump()),before)
    def tick(self,post):
        return sync.tick(native.Reader(None,self.board),self.j,post,changes=None,
                         plane='urn:private:knowledge',actor='fixture',completion=True)
    def test_unrelated_item_drains_without_any_retired_read_or_replay(self):
        self.retire();other='demo-other'
        self.j.db.execute('INSERT INTO pending(id,tx) VALUES(?,?)',(other,12));calls=[];description=[]
        def post(endpoint,body,**kwargs):
            calls.append(endpoint)
            self.assertNotIn(ID,json.dumps(body))
            if endpoint=='/knot':
                description.append(json.loads(self.j.next()[2])['description'])
                return {'replaced':True,'conforms':True,'tx_id':42}
            query=body['query']
            if 'BIND(' in query:
                return {'rows':[dict(r,s=native.ITEM+other) for r in rows(**{native.SCHEMA+'identifier':other})],'truncated':False}
            if query.startswith('ASK '):return {'result':True,'truncated':False}
            return {'rows':[{'v':description[0]}],'truncated':False}
        result=self.tick(post)
        self.assertEqual((result['status'],result['verified'],result['writes'],result['backlog'],result['retired_effects']),('OK',1,1,0,1))
        self.assertEqual(calls.count('/knot'),1)
        self.assertEqual(self.j.db.execute('SELECT body FROM retired_effect').fetchone()[0],self.original)
    def test_inverse_second_unknown_still_freezes_and_stops_the_tick(self):
        self.retire();other='demo-second'
        self.j.db.execute('INSERT INTO pending(id,tx) VALUES(?,?)',(other,12))
        self.j.prepare(other,12,{**self.env,'write_id':'b'*64})
        calls=[]
        def post(endpoint,body,**kwargs):
            calls.append(endpoint)
            if endpoint=='/knot/status':return {'contract':'quipu.knot-completion/v1','write_id':'b'*64,'state':'unknown'}
            self.assertEqual(endpoint,'/query');return {'result':False,'truncated':False}
        r=self.tick(post)
        self.assertEqual((r['status'],r['writes'],r['retired_effects']),('UNKNOWN',0,1))
        self.assertEqual(r['reason'],'pending effect indeterminate; freeze, no resend')
        self.assertIsNotNone(self.j.next()[2]);self.assertNotIn('/knot',calls)
        with self.assertRaises(custody.CustodyUnknown):
            self.j.retire_effect(other,write_id='b'*64,request_sha256=self.digest,version=1,effect_tx=12,reason='second',now=101)
    def test_operator_release_only_restores_frozen_pending_never_resends(self):
        self.retire()
        with self.assertRaises(custody.CustodyUnknown):
            self.j.release_retired(ID,write_id='b'*64,request_sha256=self.digest,reason='operator')
        self.j.release_retired(ID,write_id='a'*64,request_sha256=self.digest,reason='operator release')
        self.assertEqual(self.j.retired_count(),0);self.assertEqual(self.j.next()[2],self.original)
        self.assertEqual(self.j.db.execute('SELECT body,release_reason FROM retired_effect').fetchone(),(self.original,'operator release'))
        def post(endpoint,body,**kwargs):
            if endpoint=='/knot/status':return {'contract':'quipu.knot-completion/v1','write_id':'a'*64,'state':'unknown'}
            self.assertEqual(endpoint,'/query');return {'result':False,'truncated':False}
        self.assertEqual(self.tick(post)['status'],'UNKNOWN')
    def test_already_verified_effect_cannot_be_hidden(self):
        self.j.db.execute('INSERT INTO verified VALUES(?,?)',(ID,1))
        with self.assertRaises(custody.CustodyUnknown):self.retire()
        self.assertEqual(self.j.retired_count(),0)
    def test_cli_lock_authority_and_off_controls_never_call_server(self):
        base=['--state',str(self.path)]
        authority={'format':'camayoc-tracker-authority-v1','backend':'seeds',
                   'server':self.server,'graph':GRAPH,'prefix':'demo'}
        def run(action,extra=(),profile=None):
            stdin=io.TextIOWrapper(io.BytesIO(json.dumps(profile or authority).encode()))
            with patch.object(cli.sys,'stdin',stdin),patch.object(cli.planes,'SERVER',self.server), \
                 patch.object(cli.planes,'_post',return_value={'result':False,'truncated':False}):
                return cli.main([action,*base,*extra])
        before=list(self.j.db.iterdump())
        self.assertEqual(run('retire'),0)  # OFF, no authority read or mutation
        enabled=['--enable','--native-authority-stdin']
        with self.path.with_suffix('.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.assertEqual(run('inspect',enabled),2)
        self.assertEqual(run('inspect',enabled,{**authority,'server':'https://foreign.example.test'}),3)
        self.assertEqual(list(self.j.db.iterdump()),before)
        self.j.db.execute('CREATE TABLE backfill(foreign_adapter TEXT)')
        before=list(self.j.db.iterdump())
        self.assertEqual(run('inspect',enabled),3)
        self.assertEqual(list(self.j.db.iterdump()),before)
        self.j.db.execute('DROP TABLE backfill')
        binding=['--item',ID,'--write-id','a'*64,'--request-sha256',self.digest,
                 '--version','1','--effect-tx','11','--reason','operator fixture']
        self.assertEqual(run('retire',[*enabled,*binding]),0)
        self.assertEqual(run('inspect',enabled),0)
        self.assertEqual(self.j.retired_count(),1)
        self.assertEqual(run('release',[*enabled,*binding]),0)
        self.assertEqual(self.j.next()[2],self.original)

    def test_metric_and_bounded_inspection_preserve_uncertainty_visibility(self):
        self.retire();r=cli.operate(self.j,SimpleNamespace(action='inspect'))
        self.assertEqual(r['retired_effects'],1);self.assertNotIn('body',r['records'][0])
        self.assertEqual(r['records'][0]['request_sha256'],self.digest)
        with patch.object(scheduled.camayoc_metrics,'push',return_value=(True,'ok')) as push:
            code=scheduled.publish_native_receipt(SimpleNamespace(publish_status=True,actor='fixture'),
                 {'status':'OK','retired_effects':1,'backlog':0},100,100,GRAPH)
        self.assertEqual(code,0)
        self.assertIn('camayoc_workitem_ingress_retired_effects 1',push.call_args.args[1])


if __name__=='__main__':unittest.main()
