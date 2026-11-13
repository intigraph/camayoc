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
        args=dict(write_id='a'*64,request_sha256=self.digest,version=1,effect_tx=11,reason='operator task:test',now=100)
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
    def test_one_slot_only_and_foreign_journal_refused(self):
        self.retire()
        with self.assertRaises(custody.CustodyUnknown):self.retire()
        with self.assertRaises(custody.CustodyUnknown):
            custody.Journal(self.path,self.board,server='https://other.example.test')
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
                 patch.object(cli.planes,'_post',side_effect=AssertionError('no network')):
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
