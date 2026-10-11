import json
import tempfile
import unittest
from pathlib import Path
from test_seed_work_items import GRAPH,ID,rows
from test_seed_change_custody import page
import seed_work_items as nw
import seed_change_custody as sc
import seed_work_item_sync as sync

class Tick(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'journal.db'
        self.reader=nw.Reader(lambda *args:None,nw.Board(GRAPH,'demo'))
        self.j=sc.Journal(self.path,self.reader.board,initial_tx=9)
        self.writes=0;self.description=None;self.unavailable=False;self.drop=False
    def tearDown(self):self.j.db.close();self.tmp.cleanup()
    def post(self,endpoint,body):
        if endpoint=='/knot':
            self.writes+=1
            stored=json.loads(self.j.next()[2]);self.description=stored['description']
            if self.drop:raise TimeoutError('committed, response lost')
            return {'replaced':True,'tx_id':99,'conforms':True}
        if self.unavailable:raise TimeoutError('readback unavailable')
        query=body['query']
        if 'BIND(' in query:return {'rows':rows(),'truncated':False}
        if 'SELECT ?t' in query:return {'rows':[{'t':'aegis:WorkItem'}],'truncated':False}
        return {'rows':[{'v':self.description}] if self.description else [],'truncated':False}
    def tick(self):
        return sync.tick(self.reader,self.j,self.post,changes=lambda **args:page() if args['since']<10 else page(records=[],next_tx=args['since'],watermark_tx=args['since']),plane='urn:private:knowledge',actor='fixture')
    def test_full_tick_five_calls_one_effect_and_ack(self):
        # Full IRI control; CURIE expansion is namespace-specific.
        original=self.post
        def post(endpoint,body):
            value=original(endpoint,body)
            if 'SELECT ?t' in body.get('query',''):value['rows']=[{'t':'http://aegis.gastown.local/ontology/WorkItem'}]
            return value
        self.post=post
        result=self.tick()
        self.assertEqual((result['status'],result['writes'],result['requests'],result['verified']),('OK',1,5,1))
        self.assertIsNone(self.j.next())
    def test_commit_response_loss_freezes_without_resend_across_restart(self):
        self.drop=True;self.assertEqual(self.tick()['status'],'UNKNOWN')
        self.assertEqual(self.writes,1);self.assertIsNotNone(self.j.next()[2])
        self.j.db.close();self.j=sc.Journal(self.path,self.reader.board)
        self.unavailable=True;self.assertEqual(self.tick()['status'],'UNKNOWN');self.assertEqual(self.writes,1)
        self.unavailable=False
        original=self.post
        def post(endpoint,body):
            value=original(endpoint,body)
            if 'SELECT ?t' in body.get('query',''):value['rows']=[{'t':'http://aegis.gastown.local/ontology/WorkItem'}]
            return value
        self.post=post
        self.assertEqual(self.tick()['verified'],1);self.assertEqual(self.writes,1)
    def test_same_source_version_repeated_candidate_is_verified_without_write(self):
        self.assertEqual(self.tick()['verified'],1)
        self.j.retain(page(11))
        result=self.tick()
        self.assertEqual((result['status'],result['writes'],result['verified']),('OK',0,1))
        self.assertEqual(self.writes,1)

    def test_unproven_control_never_acknowledges(self):
        original=self.post
        def bad_control(endpoint,body):
            result=original(endpoint,body)
            if 'SELECT ?t' in body.get('query',''):result['rows']=[{'t':'unknown:WorkItem'}]
            return result
        self.post=bad_control
        result=self.tick();self.assertEqual(result['status'],'UNKNOWN')
        self.assertIsNotNone(self.j.next()[2]);self.assertEqual(self.writes,1)
    def test_bad_native_identity_does_not_prepare_or_write(self):
        def wrong(endpoint,body):return {'rows':rows(**{nw.SCHEMA+'identifier':'demo-wrong'}),'truncated':False}
        self.post=wrong;result=self.tick();self.assertEqual(result['status'],'UNKNOWN')
        self.assertEqual(self.writes,0);self.assertIsNone(self.j.next()[2])
