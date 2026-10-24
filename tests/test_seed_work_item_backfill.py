"""Offline native source coverage and failure/custody controls."""
import copy
import json
import sqlite3
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import io
from test_seed_work_items import rows, GRAPH, ID
import seed_work_items as nw
import seed_change_custody as custody
import seed_work_item_backfill as backfill

PEER='https://peer.example.test'
PLANE='urn:private:knowledge'


class Backfill(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'backfill.sqlite3'
        self.board=nw.Board(GRAPH,'demo');self.reader=nw.Reader(None,self.board)
        self.j=backfill.Sweep(self.path,self.board,server=PEER,plane=PLANE,actor='fixture',initialise=True)
        self.ids=['demo-%03d'%i for i in range(19)]
        self.descriptions={};self.effects=[];self.drop=False;self.bad=None;self.seen=[]
        self.foreign={'untouched':'other-source'}
    def tearDown(self):
        self.j.db.close();self.tmp.cleanup()
    def post(self,endpoint,body):
        self.seen.append((endpoint,copy.deepcopy(body)))
        if endpoint=='/knot':
            item,raw=next((i,b) for i,b in self.j.db.execute('SELECT id,body FROM pending WHERE body IS NOT NULL') if json.loads(b)['request']==body)
            envelope=json.loads(raw)
            self.effects.append(item);self.descriptions[item]=envelope['description']
            self.assertTrue(body['replace_snapshot']);self.assertTrue(body['snapshot'].startswith('camayoc-seeds-metadata:'))
            self.assertEqual(body['source'],nw.ITEM+item)
            if self.drop:raise TimeoutError('secret must not appear')
            return {'replaced':True,'conforms':True,'tx_id':42}
        q=body['query']
        if 'SELECT ?id' in q:
            if self.bad: return self.bad
            after=self.j.db.execute('SELECT after_id FROM backfill').fetchone()[0]
            return {'rows':[{'id':i} for i in self.ids if i>after][:backfill.PAGE+1],'truncated':False}
        item=next(i for i in self.ids if '<'+nw.ITEM+i+'>' in q or '<http://aegis.gastown.local/ontology/'+i+'>' in q)
        if 'BIND(' in q:
            record=rows(**{nw.SCHEMA+'identifier':item,nw.SEEDS+'status':'closed' if item.endswith('0') else 'deferred',nw.QUECHUA+'blockedOn':nw.ITEM+'demo-blocker'})
            for r in record:r['s']=nw.ITEM+item
            return {'rows':record,'truncated':False}
        if q.startswith('ASK '):return {'result':True}
        return {'rows':[{'v':self.descriptions[item]}] if item in self.descriptions else [],'truncated':False}
    def tick(self):return backfill.tick(self.reader,self.j,self.post,plane=PLANE,actor='fixture')
    def exact(self, item='demo-000', initialise=True):
        self.j.db.close()
        self.path=Path(self.tmp.name)/'exact.sqlite3'
        self.j=backfill.Sweep(self.path,self.board,server=PEER,plane=PLANE,
                              actor='fixture',initialise=initialise,item=item)

    def test_exact_selection_never_discovers_or_delivers_a_second_id(self):
        self.exact()
        r=self.tick()
        self.assertEqual((r['status'],r['writes'],r['verified'],r['requests']),('OK',1,1,4))
        self.assertFalse(r['enumeration_exhausted'])
        self.assertEqual(r['item'],'demo-000')
        self.assertFalse(any('SELECT ?id' in b.get('query','') for _,b in self.seen))
        self.assertEqual(self.effects,['demo-000'])
        self.assertEqual(len(r['calls']),4)
        self.assertTrue(all(c['request_json_bytes']>0 and c['response_json_bytes']>0 for c in r['calls']))
        self.assertEqual(self.tick()['writes'],0)
        self.assertEqual(self.effects,['demo-000'])

    def test_exact_rejects_an_unselected_pending_item(self):
        self.exact()
        self.j.db.execute('DELETE FROM pending')
        self.j.db.execute('INSERT INTO pending(id,tx) VALUES(?,0)',('demo-001',))
        r=self.tick()
        self.assertEqual((r['status'],r['requests'],r['writes']),('UNKNOWN',0,0))
        self.assertFalse(self.effects)

    def test_exact_lost_response_reopens_only_for_controlled_readback(self):
        self.exact();self.drop=True
        self.assertEqual(self.tick()['status'],'UNKNOWN')
        self.assertEqual(self.effects,['demo-000'])
        self.exact(initialise=False)
        r=self.tick();self.assertEqual((r['verified'],r['writes'],r['requests']),(1,0,2))
        self.assertEqual(self.effects,['demo-000'])

    def test_exact_rejects_another_selection_and_sweep_custody_before_write(self):
        self.exact();self.tick();self.j.db.close();before=self.path.read_bytes()
        for item in ('demo-001',None,'foreign-one'):
            with self.subTest(item=item),self.assertRaises(ValueError):
                backfill.Sweep(self.path,self.board,server=PEER,plane=PLANE,actor='fixture',item=item)
            self.assertEqual(self.path.read_bytes(),before)
        self.j=backfill.Sweep(self.path,self.board,server=PEER,plane=PLANE,actor='fixture',item='demo-000')

    def test_bounded_pages_full_native_coverage_repeat_no_effect(self):
        for _ in range(8):
            r=self.tick();self.assertEqual(r['status'],'OK');self.assertLessEqual(r['requests'],17)
        self.assertEqual(set(self.effects),set(self.ids));self.assertEqual(len(self.effects),19)
        self.assertEqual(r['backlog'],0);self.assertTrue(r['enumeration_exhausted'])
        self.assertEqual(self.tick()['writes'],0)
        self.assertEqual(self.foreign,{'untouched':'other-source'})
        queries=[b['query'] for e,b in self.seen if 'SELECT ?id' in b.get('query','')]
        self.assertEqual(len(queries),2)
        self.assertTrue(all(f'GRAPH <{GRAPH}>' in q and 'LIMIT 17' in q for q in queries))
        self.assertTrue(all('/knot'==e or e=='/query' for e,_ in self.seen))
        self.assertIn('blockedOn',next(b['query'] for _,b in self.seen if 'BIND(' in b.get('query','')))
    def test_restart_lost_response_only_readback_never_resend(self):
        self.ids=self.ids[:1];self.drop=True
        self.assertEqual(self.tick()['status'],'UNKNOWN');pending=self.j.next()[2]
        self.j.db.close();self.j=backfill.Sweep(self.path,self.board,server=PEER,plane=PLANE,actor='fixture')
        self.assertEqual(self.j.next()[2],pending)
        self.assertEqual(self.tick()['verified'],1);self.assertEqual(len(self.effects),1)
    def test_missing_readback_freezes_without_second_effect(self):
        self.ids=self.ids[:1];self.drop=True;self.tick();self.descriptions.clear()
        self.assertEqual(self.tick()['status'],'UNKNOWN');self.assertEqual(len(self.effects),1)
        self.assertIsNotNone(self.j.next()[2])
    def test_truncated_corrupt_duplicate_foreign_empty_refuse_before_checkpoint(self):
        for body in [{'rows':[],'truncated':False}, {'rows':[],'truncated':True},
                     {'rows':[{'id':'foreign-one'}],'truncated':False},
                     {'rows':[{'id':'demo-a'},{'id':'demo-a'}],'truncated':False},
                     {'rows':[{'missing':'demo-a'}],'truncated':False},
                     {'rows':[{'id':'demo-z'},{'id':'demo-a'}],'truncated':False},
                     {'rows':[{'id':'demo-a'}]}]:
            with self.subTest(body=body):
                self.bad=body;self.assertEqual(self.tick()['status'],'UNKNOWN')
                self.assertEqual(self.j.db.execute('SELECT after_id FROM backfill').fetchone()[0],'')
                self.assertIsNone(self.j.next());self.assertFalse(self.effects)
    def test_oversize_and_wrong_native_identity_never_succeed(self):
        self.bad={'rows':[{'id':'demo-a'}]*(backfill.PAGE+2),'truncated':False}
        self.assertEqual(self.tick()['status'],'UNKNOWN');self.assertFalse(self.effects)
        self.bad=None;original=self.post
        def wrong(endpoint,body):
            if 'BIND(' in body.get('query',''):
                return {'rows':rows(),'truncated':False}  # wrong exact subject
            return original(endpoint,body)
        self.post=wrong
        self.assertEqual(self.tick()['status'],'UNKNOWN');self.assertFalse(self.effects)
        self.assertEqual(self.j.db.execute('SELECT COUNT(*) FROM pending').fetchone()[0],backfill.PAGE)

    def test_slow_discovery_limits_to_one_delivery(self):
        times=iter([0,3,3,3,3,3,3,3,3,3])
        result=backfill.tick(self.reader,self.j,self.post,plane=PLANE,actor='fixture',clock=lambda:next(times))
        self.assertEqual(result['status'],'OK');self.assertEqual(result['verified'],1)
        self.assertEqual(result['backlog'],15)

    def test_real_rdf_discovery_graph_scope_and_keyset(self):
        from rdflib import Dataset,URIRef,Literal,RDF
        data=Dataset();g=data.graph(URIRef(GRAPH))
        for item in self.ids:
            subject=URIRef(nw.ITEM+item);g.add((subject,RDF.type,URIRef(nw.SCHEMA+'Action')))
            g.add((subject,URIRef(nw.SCHEMA+'identifier'),Literal(item)))
        foreign=data.graph(URIRef('urn:other'))
        foreign.add((URIRef(nw.ITEM+'demo-foreign'),RDF.type,URIRef(nw.SCHEMA+'Action')))
        foreign.add((URIRef(nw.ITEM+'demo-foreign'),URIRef(nw.SCHEMA+'identifier'),Literal('demo-foreign')))
        def execute(route,body):
            return {'rows':[{'id':str(r.id)} for r in data.query(body['query'])],'truncated':False}
        ids,final=backfill.discover(execute,self.board,'')
        self.assertEqual(ids,self.ids[:16]);self.assertFalse(final)
        ids,final=backfill.discover(execute,self.board,ids[-1])
        self.assertEqual(ids,self.ids[16:]);self.assertTrue(final)

    def test_auth_error_is_unknown_not_zero_success(self):
        def denied(*args):raise PermissionError('secret')
        r=backfill.tick(self.reader,self.j,denied,plane=PLANE,actor='fixture')
        self.assertEqual(r['status'],'UNKNOWN');self.assertNotIn('secret',json.dumps(r));self.assertEqual(r['writes'],0)
    def test_foreign_server_board_plane_actor_and_reset_refuse(self):
        for changes in [dict(server='https://other.example.test'),dict(board=nw.Board('urn:other','demo')),
                        dict(plane='urn:other'),dict(actor='other'),dict(initialise=True)]:
            args=dict(board=self.board,server=PEER,plane=PLANE,actor='fixture');args.update(changes)
            with self.subTest(changes=changes),self.assertRaises((ValueError,sqlite3.Error)):
                backfill.Sweep(self.path,**args)
    def test_standing_ingress_and_missing_initialisation_refuse(self):
        other=Path(self.tmp.name)/'ingress.sqlite3';j=custody.Journal(other,self.board,server=PEER,initial_tx=7);j.db.close()
        before=other.read_bytes()
        with self.assertRaises(sqlite3.Error):backfill.Sweep(other,self.board,server=PEER,plane=PLANE,actor='fixture')
        self.assertEqual(other.read_bytes(),before)
        with self.assertRaises(ValueError):backfill.Sweep(Path(self.tmp.name)/'new.sqlite3',self.board,server=PEER,plane=PLANE,actor='fixture')
    def test_default_off_no_io_or_secret_output(self):
        dest=Path(self.tmp.name)/'absent'/'state.sqlite3'
        p=subprocess.run([sys.executable,str(Path(backfill.__file__)),'--state',str(dest),'--actor','fixture'],text=True,capture_output=True)
        self.assertEqual(p.returncode,0,p.stderr);self.assertEqual(json.loads(p.stdout)['status'],'OFF');self.assertFalse(dest.parent.exists())


class BoundedTransport(unittest.TestCase):
    def test_response_cap_is_enforced_during_read(self):
        import planes
        for limit,content,ok in [(100,b'{"rows":[]}',True),(10,b'x'*100,False)]:
            with patch.object(planes,'auth_token',return_value=None), patch.object(planes,'provenance_headers',return_value={}), patch.object(planes.urllib.request,'urlopen',return_value=io.BytesIO(content)) as opened:
                if ok:self.assertEqual(planes._post('/query',{},client='fixture',response_limit=limit),{'rows':[]})
                else:
                    with self.assertRaises(planes.PlaneError):planes._post('/query',{},client='fixture',response_limit=limit)
                self.assertEqual(opened.call_count,1)
        with patch.object(planes.urllib.request,'urlopen') as opened:
            with self.assertRaises(planes.PlaneError):planes._post('/query',{},client='fixture',response_limit=0)
            opened.assert_not_called()

if __name__=='__main__':unittest.main()
