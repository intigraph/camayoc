import copy
from datetime import datetime,timezone
import json
from pathlib import Path
import re
import sys
import tempfile
from unittest.mock import patch
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from test_usage_generation_codec import CodecTests
import usage_generation_codec as c
import usage_generation_wire as w
from usage_generation_reader import read_snapshot
import publish_work_cost as publisher


PRODUCER='https://example.org/producer'
AT='2026-10-24T00:00:00Z'
NOW=datetime(2026,10,24,tzinfo=timezone.utc).timestamp()


def records(n=12):
    return [CodecTests.record('attributed')|{'id':str(i),'bead':'p-'+str(i)} for i in range(n)]


def planned(rows=None):
    return w.plan(records() if rows is None else rows,producer=PRODUCER,harness='codex',
                  session='s',at=AT,actor='fixture')


def response(quads):
    rows=[]
    for g,s,p,o in sorted(set(quads),key=repr):
        row={'s':s,'p':p,'lex':o[1],'iri':o[0]=='uri'}
        if g is not None:row['g']=g
        if o[0]=='literal':row.update(datatype=o[2],lang='')
        rows.append(row)
    return {'rows':rows,'count':len(rows),'truncated':False}


def all_response(p):
    q=[(None,s,pred,tuple(o)) for s,pred,o in p['activation_triples']]
    q += [(p['generation'],s,pred,tuple(o)) for block in [*p['part_triples'],p['metadata_triples']] for s,pred,o in block]
    return response(q)


class ProtocolTests(unittest.TestCase):
    def read(self,r):return read_snapshot(r,producer=PRODUCER,harness='codex',session='s',at=AT)

    def test_complete_oracle_and_all_attribution_states(self):
        rows=records()+[CodecTests.record('no proven focus')|{'id':'unknown'},
                        CodecTests.record('overlapping focus')|{'id':'overlap'}]
        p=planned(rows);r=self.read(all_response(p))
        self.assertEqual(r['rows'],c.prepare_rows(rows));self.assertEqual(r['status'],'COMPLETE')
        self.assertEqual(p['manifest']['source_sha256'],c.digest(r['rows']))

    def test_missing_tampered_duplicate_wrong_kind_and_legacy_refuse(self):
        original=all_response(planned())
        missing=copy.deepcopy(original);missing['rows'].pop();missing['count']-=1
        duplicate=copy.deepcopy(original);duplicate['rows'].append(duplicate['rows'][0]);duplicate['count']+=1
        changed=copy.deepcopy(original)
        next(r for r in changed['rows'] if r['p']==c.ONTOLOGY+'tokensConsumed')['lex']='999'
        kind=copy.deepcopy(original);next(r for r in kind['rows'] if r['p']==c.ONTOLOGY+'tokensConsumed')['datatype']=w.XSD+'string'
        legacy=copy.deepcopy(original);next(r for r in legacy['rows'] if 'g' in r)['g']='https://example.org/legacy'
        for r in [missing,duplicate,changed,kind,legacy,original|{'truncated':True}]:
            with self.subTest(response=r),self.assertRaises(ValueError):self.read(r)

    def test_body_and_record_caps_with_single_oversize_refusal(self):
        p=planned(records(81));self.assertEqual(sum(d[2] for d in p['manifest']['parts']),81)
        self.assertTrue(all(d[2]<=40 and d[3]<=65536 for d in p['manifest']['parts']))
        self.assertTrue(all(w.byte_size(b)<=65536 for b in p['parts']))
        self.assertLessEqual(w.byte_size(p['activation']),2048)
        with self.assertRaises(ValueError):planned([CodecTests.record()|{'model':'x'*65536}])
        with self.assertRaises(ValueError):planned([CodecTests.record()|{'at':'2026-10-25T00:00:00Z'}])
        empty=planned([]);self.assertEqual(self.read(all_response(empty))['rows'],[])

    def test_reassignment_changes_generation_not_request_identity(self):
        old=records();new=copy.deepcopy(old);new[0]['bead']='p-other'
        p1=planned(old);p2=planned(new)
        self.assertNotEqual(p1['generation'],p2['generation'])
        self.assertEqual(c.record_iri(p1['rows'][0]),c.record_iri(p2['rows'][0]))
        got=self.read(all_response(p2));self.assertNotIn(c.ONTOLOGY+'p-0',got['rows'][0][8])


class FixtureTransport:
    def __init__(self):self.graphs={};self.snapshots={};self.calls=[];self.labels={}
    def __call__(self,ep,body,**kwargs):
        from rdflib import Graph,URIRef,Literal
        self.calls.append((ep,body))
        if ep=='/shapes':return {'turtle':(Path(__file__).resolve().parents[1]/'shapes/usage-generations.shapes.ttl').read_text()}
        if ep=='/graph/create':
            self.graphs.setdefault(body['graph'],set());return {'g':list(self.graphs).index(body['graph'])+1,'graph':body['graph']}
        if ep=='/graph/label':
            self.labels[body['graph']]={'kind':{'coverage':'full','value':['knowledge']}};return {'tx_id':len(self.calls)}
        if ep=='/knot':
            g=Graph().parse(data=body['turtle'],format='turtle');triples=set()
            for s,p,o in g:
                term=('uri',str(o),None) if isinstance(o,URIRef) else ('literal',str(o),str(o.datatype or URIRef(w.XSD+'string')))
                triples.add((str(s),str(p),term))
            self.snapshots[(body['graph'],body['snapshot'])]=triples
            self.graphs[body['graph']]=set().union(*(t for (g,_),t in self.snapshots.items() if g==body['graph']))
            return {'conforms':True,'tx_id':len(self.calls)}
        assert ep=='/query'
        q=body['query']
        if 'VALUES ?item' in q:
            items=re.findall(r'<([^>]+)>',q.split('VALUES ?item {')[1].split('}')[0])
            return {'rows':[{'item':i} for i in items],'truncated':False}
        graph=body.get('graph') or re.search(r'GRAPH <([^>]+)>',q).group(1)
        if 'usageSelectedGeneration' in q:
            controls=self.graphs[graph];gens={o[1] for _,p,o in controls if p==c.ONTOLOGY+'usageSelectedGeneration'}
            return response([(None,s,p,o) for s,p,o in controls]+[(g,s,p,o) for g in gens for s,p,o in self.graphs[g]])
        triples=self.graphs[graph]
        if 'VALUES ?s' in q:
            subjects=set(re.findall(r'<([^>]+)>',q.split('VALUES ?s {')[1].split('}')[0]))
            return response([(None,s,p,o) for s,p,o in triples if s in subjects])
        return {'rows':[{'s':next(iter(triples))[0]}] if triples else [],'truncated':False,'labels':self.labels.get(graph,{})}


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.state=Path(self.tmp.name)/'state.json';self.transport=FixtureTransport()
        auth=Path(self.tmp.name)/'authority.json';auth.write_text(json.dumps({'publish_work_cost.py':['read:crew:records']}))
        self.env=patch.dict('os.environ',{'CAMAYOC_AUTHORITY':str(auth)});self.env.start();self.addCleanup(self.env.stop)
        self.sleep=patch('time.sleep');self.sleep.start();self.addCleanup(self.sleep.stop)
        self.policy={'enabled':True,'producer':PRODUCER,'harness':'codex','session':'s',
                     'legacy_frozen':True,'runtime_acceptance':'fixture-only-not-an-enrollment'}
        self.now=NOW

    def tick(self,rows):
        self.now+=61
        before=len(self.transport.calls)
        with patch('time.time',return_value=self.now),patch('planes._post',side_effect=self.transport):
            answer=publisher.publish({'errors':[],'records':rows},'fixture',self.state,generation_policy=self.policy)
        calls=self.transport.calls[before:]
        self.assertLessEqual(len([e for e,_ in calls if e!='/query' and e!='/shapes']),1)
        self.assertLessEqual(len(calls),3)
        return answer

    def test_full_ticks_then_correction_then_retention_fence(self):
        rows=records()
        for _ in range(8):self.tick(rows)
        journal=json.loads(self.state.with_suffix('.generations.json').read_text())
        entry=next(iter(journal['sessions'].values()));self.assertIsNone(entry['progress']);self.assertIsNotNone(entry['selected'])
        old=entry['selected']['generation'];self.tick(rows)
        with patch('time.time',return_value=self.now+61),patch('planes._post',side_effect=self.transport):
            with self.assertRaisesRegex(ValueError,'frozen'):
                publisher.publish({'errors':[],'records':rows[:2]},'fixture',self.state)
        self.now+=61
        changed=copy.deepcopy(rows);changed[0]['bead']='p-other'
        for _ in range(7):self.tick(changed)
        journal=json.loads(self.state.with_suffix('.generations.json').read_text());entry=next(iter(journal['sessions'].values()))
        self.assertNotEqual(entry['selected']['generation'],old);self.assertEqual(entry['retained'][0]['generation'],old)
        changed[1]['bead']='p-third'
        with self.assertRaisesRegex(ValueError,'retention'):self.tick(changed)

    def test_lost_local_cursor_uses_exact_verified_receipt_without_resend(self):
        rows=records()
        for _ in range(5):self.tick(rows)
        journal=self.state.with_suffix('.generations.json');before=journal.read_bytes()
        self.tick(rows)
        journal.write_bytes(before)  # private fixture simulates crash before cursor commit
        calls=len(self.transport.calls)
        self.assertEqual(self.tick(rows),[])
        self.assertEqual(len(self.transport.calls),calls)
        receipt=json.loads(self.state.with_suffix('.receipt.json').read_text())
        self.assertTrue(receipt['recovered_cursor']);self.assertEqual(receipt['requests'],0)
        self.assertEqual(receipt['request_seconds'],0);self.assertEqual(receipt['knot_seconds'],0)

    def test_corrupt_artifact_and_forged_selected_state_refuse_without_requests(self):
        rows=records();self.tick(rows);self.tick(rows)
        path=self.state.with_suffix('.generations.json');original=path.read_text()
        j=json.loads(original);entry=next(iter(j['sessions'].values()))
        entry['progress']['plan']['manifest']['records']+=1
        path.write_text(json.dumps(j));before=len(self.transport.calls)
        with self.assertRaises(ValueError):self.tick(rows)
        self.assertEqual(before,len(self.transport.calls))
        path.write_text(original)

    def test_policy_and_pending_fences(self):
        self.policy['runtime_acceptance']=''
        with self.assertRaises(ValueError):self.tick(records())
        self.assertEqual(self.transport.calls,[])
        self.policy['runtime_acceptance']='fixture'
        self.state.with_suffix('.pending.json').write_text('{"preserve":"unknown"}')
        with self.assertRaisesRegex(ValueError,'INDETERMINATE'):self.tick(records())
        self.assertEqual(self.transport.calls,[])


class ConsumerTests(unittest.TestCase):
    def test_global_consumer_attempt_and_failure_preserve_unknown(self):
        from usage_generation_reader import consume
        with tempfile.TemporaryDirectory() as d:
            state=Path(d)/'consumer.json';calls=[]
            def post(*args,**kwargs):calls.append(args);return all_response(planned())
            args={'producer':PRODUCER,'harness':'codex','session':'s','at':AT,'state_path':state,'post':post}
            first=consume(**args,now=NOW);self.assertEqual(first['status'],'COMPLETE')
            second=consume(**(args|{'session':'other'}),now=NOW+1)
            self.assertEqual(second['status'],'UNKNOWN');self.assertEqual(len(calls),1)
            third=consume(**args,now=NOW+300);self.assertEqual(third['status'],'COMPLETE')
            self.assertEqual(len(calls),2)


class RetrievalRouteTests(unittest.TestCase):
    def test_generation_method_scope_and_fixed_consumer_cache(self):
        from retrieve_metric import execute
        params={'producer':PRODUCER,'harness':'codex','session':'s','horizon':AT,'consumer':'brief'}
        with tempfile.TemporaryDirectory() as d,patch.dict('os.environ',{'CAMAYOC_COST_READ_STATE':d}),patch('usage_generation_reader.consume',return_value={'status':'UNKNOWN'}) as consume:
            result=execute({'system':'session_usage','query':'work_cost_generation','params':params})
            self.assertEqual(result['status'],'unknown')
            self.assertEqual(consume.call_args.kwargs['state_path'],Path(d)/'brief.json')
            consume.reset_mock()
            for bad in [params|{'consumer':'new-poller'},params|{'state_path':'arbitrary'}]:
                self.assertEqual(execute({'system':'session_usage','query':'work_cost_generation','params':bad})['status'],'invalid')
            consume.assert_not_called()
