import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import usage_generation_step as s


class StepTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.state=Path(self.tmp.name)/'state.json';self.calls=[]
        self.sleep=patch.object(s.time,'sleep');self.sleep.start();self.addCleanup(self.sleep.stop)

    def post(self, endpoint, body):
        self.calls.append((endpoint,body))
        if endpoint!='/query':
            pending=self.state.with_suffix('.pending.json')
            self.assertTrue(pending.exists())
            self.assertEqual(json.loads(pending.read_text())['body'],body)
            return {'tx_id':0,'conforms':True}
        return {'rows':[{'id':'positive'}]}

    def test_three_requests_zero_tx_and_rate_shared(self):
        r=s.run_step('part',{'turtle':'fixture'},self.state,now=100,post=self.post,
                     records=1,preflight=lambda call:bool(call('/query',{})['rows']),
                     verify=lambda call,r:bool(call('/query',{})['rows']))
        self.assertEqual(r['status'],'VERIFIED');self.assertEqual(len(self.calls),3)
        self.assertFalse(self.state.with_suffix('.pending.json').exists())
        with self.assertRaises(ValueError):
            s.run_step('whole',{},self.state,now=159,post=self.post,verify=lambda *_:True)
        self.assertEqual(len(self.calls),3)

    def test_marker_two_requests(self):
        r=s.run_step('marker',{},self.state,now=100,post=self.post,
                     verify=lambda call,r:bool(call('/query',{})['rows']))
        self.assertEqual(r['requests'],2)

    def test_lost_reply_never_resends_or_drops_pending(self):
        def lost(endpoint,body): self.post(endpoint,body);raise TimeoutError('lost reply')
        with self.assertRaises(TimeoutError):
            s.run_step('part',{},self.state,now=100,post=lost,records=1,verify=lambda *_:True)
        before=self.state.with_suffix('.pending.json').read_bytes()
        with self.assertRaisesRegex(ValueError,'INDETERMINATE'):
            s.run_step('part',{},self.state,now=1000,post=self.post,records=1,verify=lambda *_:True)
        self.assertEqual(len(self.calls),1)
        self.assertEqual(before,self.state.with_suffix('.pending.json').read_bytes())
        self.assertEqual(len(json.loads(self.state.with_suffix('.writes.json').read_text())),1)

    def test_refusal_and_readback_mismatch_stall(self):
        for accepted in [{'conforms':False},{},{'tx_id':1}]:
            with tempfile.TemporaryDirectory() as d:
                state=Path(d)/'state.json'
                with self.assertRaises(ValueError):
                    s.run_step('part',{},state,now=100,post=lambda *_:accepted,
                               records=1,verify=lambda *_:False)
                self.assertTrue(state.with_suffix('.pending.json').exists())

    def test_budget_and_preflight_refuse_before_write(self):
        for size in [65537,70000]:
            with self.assertRaises(ValueError):
                s.run_step('part',{'turtle':'x'*size},self.state,now=100,post=self.post,
                           records=1,verify=lambda *_:True)
        with self.assertRaises(ValueError):
            s.run_step('part',{},self.state,now=100,post=self.post,records=1,
                       preflight=lambda _:False,verify=lambda *_:True)
        self.assertEqual(self.calls,[])
        self.assertFalse(self.state.with_suffix('.pending.json').exists())

    def test_exclusive_writer_and_corrupt_ledger(self):
        with s.locked(self.state):
            with self.assertRaisesRegex(ValueError,'busy'):
                s.run_step('part',{},self.state,now=100,post=self.post,records=1,verify=lambda *_:True)
        self.state.with_suffix('.writes.json').write_text('{bad')
        with self.assertRaises(ValueError):
            s.run_step('part',{},self.state,now=100,post=self.post,records=1,verify=lambda *_:True)
        self.assertEqual(self.calls,[])
