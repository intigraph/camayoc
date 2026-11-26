"""aegis-xdvg.5: retire --superseded, real durable custody and the real proof path.

The receiving server and its /metrics are scripted; the journal, the binding
CAS and effect_not_applied are the production code.
"""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import seed_change_custody as custody
import seed_work_items as native
import retire_work_item_effect as cli
from test_seed_work_items import GRAPH,ID

TARGET='https://camayoc.local/plane/crew/records'


def description(version):
    return json.dumps({'id':ID,'source_graph':GRAPH,'native_action':'update','source_version':version},sort_keys=True)


class Superseded(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'journal.sqlite3'
        self.board=native.Board(GRAPH,'demo')
        self.j=custody.Journal(self.path,self.board,server='https://peer.example.test',initial_tx=10)
        self.env={'request':{'graph':TARGET,'exact':'v13'},'write_id':'d'*64,'version':13,
                  'description':description(13)}
        self.j.db.execute('INSERT INTO pending(id,tx) VALUES(?,?)',(ID,11))
        self.j.prepare(ID,11,self.env)
        self.digest=hashlib.sha256(json.dumps(self.env['request'],sort_keys=True).encode()).hexdigest()
        self.observed=4;self.starts=[200.0,200.0];self.queries=[]
    def tearDown(self):self.j.db.close();self.temp.cleanup()

    def post(self,endpoint,body):
        q=body['query'];self.queries.append(q)
        if q.startswith('ASK'):return {'result':True,'truncated':False}   # subject present
        if 'SELECT ?version ?v ?datatype' in q:
            return {'rows':[{'version':self.observed,'v':description(self.observed),'datatype':'integer'}],
                    'truncated':False}
        return {'truncated':True}                                          # no_snapshot: unproved
    def get(self,path):
        assert path=='/metrics'
        return b'# HELP x\nprocess_start_time_seconds %d\nother 1\n' % self.starts.pop(0)
    def args(self,**kw):
        a=dict(item=ID,write_id='d'*64,request_sha256=self.digest,version=13,effect_tx=11,
               reason='operator task:test',ruling='aegis-xdvg.5',sent_before=None,superseded=True)
        a.update(kw);return SimpleNamespace(**a)
    def retire(self,now=300.0,**kw):
        args=self.args(**kw)
        proof,reason=cli.superseded_proof(self.j,args,self.post,now,get=self.get)
        self.j.retire_effect(ID,write_id=args.write_id,request_sha256=args.request_sha256,version=args.version,
                             effect_tx=args.effect_tx,reason=reason,now=now,prove_superseded=proof)
        return reason
    def assert_untouched(self):
        self.assertEqual(self.j.retired_count(),0)
        self.assertEqual(self.j.next()[0],ID)

    def test_recorded_send_older_version_and_later_process_retires_once(self):
        self.j.record_send_ended(ID,'d'*64,100.0)
        reason=self.retire()
        self.assertEqual(self.j.retired_count(),1)
        self.assertIsNone(self.j.next())                       # excluded, not acknowledged or deleted
        self.assertEqual(self.j.db.execute('SELECT body FROM pending').fetchone()[0],
                         self.j.db.execute('SELECT body FROM retired_effect').fetchone()[0])
        self.assertIn('aegis-xdvg.5 superseded',reason);self.assertIn('(journal)',reason)
        self.assertEqual(self.starts,[])                        # start read twice

    def test_operator_bound_for_a_legacy_effect_without_a_record(self):
        reason=self.retire(sent_before=150.0)
        self.assertEqual(self.j.retired_count(),1);self.assertIn('(operator)',reason)

    def test_receiving_process_not_newer_than_the_send_refuses(self):
        self.j.record_send_ended(ID,'d'*64,250.0)
        with self.assertRaises(custody.CustodyUnknown):self.retire()
        self.assert_untouched()
        self.assertEqual(self.queries,[])                       # no graph read without worker-gone proof

    def test_restart_between_the_two_start_reads_refuses(self):
        self.j.record_send_ended(ID,'d'*64,100.0);self.starts=[200.0,260.0]
        with self.assertRaises(custody.CustodyUnknown):self.retire()
        self.assert_untouched()

    def test_present_at_the_same_or_newer_version_refuses(self):
        self.j.record_send_ended(ID,'d'*64,100.0)
        for observed in (13,14):
            with self.subTest(observed=observed):
                self.observed=observed;self.starts=[200.0,200.0]
                with self.assertRaises(custody.CustodyUnknown):self.retire()
                self.assert_untouched()

    def test_absent_subject_uses_the_ordinary_retirement_not_this_one(self):
        self.j.record_send_ended(ID,'d'*64,100.0)
        self.post=lambda endpoint,body:{'result':False,'truncated':False}
        with self.assertRaises(custody.CustodyUnknown):self.retire()
        self.assert_untouched()

    def test_missing_or_contradicting_send_bound_and_missing_ruling_refuse(self):
        with self.assertRaises(custody.CustodyUnknown):self.retire()              # no record, no operator bound
        self.j.record_send_ended(ID,'d'*64,120.0)
        with self.assertRaises(custody.CustodyUnknown):self.retire(sent_before=110.0)  # operator earlier than journal
        with self.assertRaises(custody.CustodyUnknown):self.retire(ruling=None)
        with self.assertRaises(custody.CustodyUnknown):self.retire(sent_before=400.0)  # in the future
        self.assert_untouched()

    def test_binding_drift_still_refuses(self):
        self.j.record_send_ended(ID,'d'*64,100.0)
        for delta in ({'write_id':'e'*64},{'version':12},{'effect_tx':12},{'request_sha256':'0'*64}):
            with self.subTest(delta=delta):
                self.starts=[200.0,200.0]
                with self.assertRaises(custody.CustodyUnknown):self.retire(**delta)
                self.assert_untouched()

    def test_both_or_neither_proof_refuses(self):
        for kw in ({},{'prove_absent':lambda i,e:True,'prove_superseded':lambda i,e:True}):
            with self.subTest(kw=list(kw)),self.assertRaises(custody.CustodyUnknown):
                self.j.retire_effect(ID,write_id='d'*64,request_sha256=self.digest,version=13,effect_tx=11,
                                     reason='x',now=1.0,**kw)
        self.assert_untouched()

    def test_metrics_without_exactly_one_start_sample_refuses(self):
        for body in (b'other 1\n',b'process_start_time_seconds 1\nprocess_start_time_seconds 2\n'):
            with self.subTest(body=body),self.assertRaises(custody.CustodyUnknown):
                cli.process_start(lambda path:body)

    def test_send_end_record_keeps_the_latest_attempt_end(self):
        self.j.record_send_ended(ID,'d'*64,100.0);self.j.record_send_ended(ID,'d'*64,90.0)
        self.assertEqual(self.j.send_ended(ID,'d'*64),100.0)
        self.assertIsNone(self.j.send_ended('other','d'*64))
        with self.assertRaises(custody.CustodyUnknown):self.j.record_send_ended(ID,'short',1.0)


if __name__=='__main__':unittest.main()
