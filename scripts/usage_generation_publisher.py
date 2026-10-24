"""Explicit opt-in staged generation ticks, sharing the existing cost cadence."""
from datetime import datetime,timezone
import hashlib
import json
import time
from pathlib import Path

import planes
import usage_generation_codec as c
from usage_generation_wire import plan,byte_size
from usage_generation_budget import admit_session
from usage_generation_step import atomic,locked,run_step
from usage_generation_terms import parse_response,snapshot_query
from usage_generation_reader import read_snapshot

PROFILE='camayoc-usage-generations'
PROFILE_FILE=Path(__file__).resolve().parents[1]/'shapes/usage-generations.shapes.ttl'


def policy_check(policy):
    required={'enabled','producer','harness','session','legacy_frozen','runtime_acceptance'}
    if not isinstance(policy,dict) or set(policy)!=required:
        raise ValueError('generation policy schema missing/unknown')
    if policy['enabled'] is not True or policy['legacy_frozen'] is not True:
        raise ValueError('generation path not enrolled/legacy publication not frozen')
    c.iri(policy['producer']);c.text(policy['session']);c.text(policy['runtime_acceptance'])
    if policy['harness'] not in ('claude','codex'):raise ValueError('unknown generation harness')


def load_state(path):
    if not path.exists():return {'version':1,'sessions':{},'profile':None}
    value=json.loads(path.read_text())
    if not isinstance(value,dict) or set(value)!={'version','sessions','profile'} or type(value['version']) is not int or value['version']!=1 or not isinstance(value['sessions'],dict):
        raise ValueError('UNKNOWN: invalid generation journal')
    if value['profile'] is not None and (not isinstance(value['profile'],str) or not c.HEX.fullmatch(value['profile'])):
        raise ValueError('UNKNOWN: invalid profile proof')
    for key,entry in value['sessions'].items():
        if not isinstance(entry,dict) or set(entry)!={'selected','progress','retained'} or not isinstance(entry['retained'],list) or len(entry['retained'])>1:
            raise ValueError('UNKNOWN: invalid generation slots')
        for selected in [entry['selected'],*entry['retained']]:
            if selected is None:continue
            required={'generation','source','horizon','bytes','producer','harness','session'}
            if not isinstance(selected,dict) or set(selected)!=required:
                raise ValueError('UNKNOWN: invalid sealed receipt')
            c.iri(selected['generation']);c.iri(selected['producer']);c.text(selected['session']);c.horizon(selected['horizon'])
            if (selected['harness'] not in ('claude','codex') or not isinstance(selected['source'],str)
                    or not c.HEX.fullmatch(selected['source']) or c.count(selected['bytes'])>c.STAGED_BYTES
                    or key!=c.digest([selected['producer'],selected['harness'],selected['session']])):
                raise ValueError('UNKNOWN: sealed identity/bytes mismatch')
        progress=entry['progress']
        if progress is not None:
            if not isinstance(progress,dict) or set(progress)!={'plan','plan_sha256','phase','part','receipts'}:
                raise ValueError('UNKNOWN: invalid staging receipt')
            actual=hashlib.sha256(json.dumps(progress['plan'],sort_keys=True).encode()).hexdigest()
            if actual!=progress['plan_sha256'] or not isinstance(progress['receipts'],list):
                raise ValueError('UNKNOWN: staging artifact hash differs')
            planned=progress['plan']
            if not isinstance(planned,dict) or not isinstance(planned.get('manifest'),dict):
                raise ValueError('UNKNOWN: invalid generation artifact')
            m=planned['manifest']
            c.validate_manifest(c.canonical(m),producer=m.get('producer'),harness=m.get('harness'),session=m.get('session'),at=m.get('horizon'))
            if (key!=c.digest([m['producer'],m['harness'],m['session']])
                    or planned.get('generation')!=c.BASE+'generation/'+c.digest(m)
                    or planned.get('control')!=c.BASE+'selector-graph/'+key
                    or planned.get('selector')!=c.BASE+'selector/'+key
                    or c.count(planned.get('bytes'))>c.STAGED_BYTES
                    or len(planned.get('parts',[]))!=len(m['parts'])
                    or progress['phase'] not in ('control','control-label','graph','graph-label','part','manifest','activate')
                    or c.count(progress['part'])>len(m['parts'])
                    or progress['phase']=='part' and progress['part']==len(m['parts'])):
                raise ValueError('UNKNOWN: staging identity/phase mismatch')
    return value


def unselected_bytes(state):
    return sum((entry['progress']['plan']['bytes'] if entry.get('progress') else 0)
               +sum(retained['bytes'] for retained in entry['retained'])
               for entry in state['sessions'].values())


def query_triples(graph,subjects):
    values=' '.join('<'+c.iri(s)+'>' for s in sorted(subjects))
    return {'query':'SELECT ?s ?p (STR(?o) AS ?lex) (isIRI(?o) AS ?iri) '
            '(DATATYPE(?o) AS ?datatype) (LANG(?o) AS ?lang) WHERE { '
            'GRAPH <'+c.iri(graph)+'> { VALUES ?s { '+values+' } ?s ?p ?o } } LIMIT 10000',
            'verbose':True}


def verify_triples(call,graph,triples):
    expected={(graph,s,p,tuple(o)) for s,p,o in triples}
    actual=set(parse_response(call('/query',query_triples(graph,{s for _,s,_,_ in expected})),graph))
    return actual==expected


def tick(result,actor,state_path,receipt,policy,*,lane_locked=False):
    policy_check(policy)
    if result.get('errors'):raise ValueError('incomplete parser input')
    state_path=Path(state_path);journal_path=state_path.with_suffix('.generations.json')
    # Separate planner lock plus shared write-step lock; C uses the latter.
    with locked(state_path.with_name(state_path.name+'.planner')):
        if state_path.with_suffix('.pending.json').exists():
            raise ValueError('INDETERMINATE: reconcile pending write')
        state=load_state(journal_path);now=receipt['attempted_at']
        expected_profile=hashlib.sha256(PROFILE_FILE.read_bytes()).hexdigest()
        def post(endpoint,body):
            if receipt['requests']>=3:raise ValueError('cost request budget')
            receipt.setdefault('first_request_at',time.time())
            receipt['requests']+=1
            started=time.monotonic()
            try:return planes._post(endpoint,body,client='camayoc-cost-generation',response_limit=c.READ_BYTES if endpoint=='/query' else 262144)
            finally:receipt['request_seconds']+=time.monotonic()-started
        if state['profile']!=expected_profile:
            loaded=post('/shapes',{'action':'get','name':PROFILE})
            if not isinstance(loaded.get('turtle'),str) or hashlib.sha256(loaded['turtle'].encode()).hexdigest()!=expected_profile:
                raise ValueError('UNKNOWN: exact governed profile not loaded')
            state['profile']=expected_profile;atomic(journal_path,state)
            receipt.update(generation_phase='profile-verified',unknown_reason='staging')
            return []
        key=c.digest([policy['producer'],policy['harness'],policy['session']])
        entry=state['sessions'].get(key,{'selected':None,'progress':None,'retained':[]})
        if set(entry)!={'selected','progress','retained'} or not isinstance(entry['retained'],list):
            raise ValueError('UNKNOWN: invalid session journal')
        source_rows=c.prepare_rows(result['records'])
        if source_rows and source_rows[0][0][:2]!=[policy['harness'],policy['session']]:
            raise ValueError('mixed generation source identity')
        source=c.digest(source_rows)
        progress=entry['progress']
        if progress is None:
            selected=entry['selected']
            if selected and selected['source']==source:
                receipt.update(generation_phase='unchanged-selected',readback='sealed-generation')
                return []
            if entry['retained']:
                raise ValueError('UNKNOWN: retained graph occupies generation slot; governed retention required')
            items={r['bead'] for r in result['records'] if r['attribution']=='attributed'}
            # Corrections stay on A after selection, including an explicitly
            # complete empty input; never resume the old whole-session writer.
            if not selected:admit_session(c_items=len(items),source_digest=source,previous={},staged_bytes=0,body_bytes=0)
            horizon=datetime.fromtimestamp(now,timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
            planned=plan(result['records'],producer=policy['producer'],harness=policy['harness'],
                         session=policy['session'],at=horizon,actor=actor,
                         previous=selected['generation'] if selected else None)
            total=unselected_bytes(state)
            if total+planned['bytes']>c.STAGED_BYTES:raise ValueError('aggregate staged bytes exceed64MiB')
            progress={'plan':planned,'plan_sha256':hashlib.sha256(json.dumps(planned,sort_keys=True).encode()).hexdigest(),'phase':'control','part':0,'receipts':[]}
            entry['progress']=progress;state['sessions'][key]=entry;atomic(journal_path,state)
        planned=progress['plan'];phase=progress['phase'];index=progress['part']
        if phase in ('control','graph'):
            graph=planned['control'] if phase=='control' else planned['generation']
            # Graph creation has its OWN tick and occupies the shared write lane.
            body={'graph':graph};kind='graph';n=0;preflight=None
            def verify(call,accepted):
                # Exact graph registry identity from the native receipt. A
                # graph-bound query must also answer, including valid empty.
                answer=call('/query',{'query':'SELECT ?s WHERE { GRAPH <'+graph+'> { ?s ?p ?o } } LIMIT 1'})
                return accepted.get('graph',graph)==graph and isinstance(answer.get('rows'),list) and answer.get('truncated') is False
        elif phase in ('control-label','graph-label'):
            graph=planned['control'] if phase=='control-label' else planned['generation']
            body={'graph':graph,'timestamp':planned['manifest']['horizon'],'kind':'knowledge','actor':actor}
            kind='graph_label';n=0;preflight=None
            def verify(call,_):
                answer=call('/query',{'graph':graph,'query':'SELECT ?s WHERE { ?s ?p ?o } LIMIT 1'})
                return answer.get('truncated') is False and answer.get('labels',{}).get('kind')=={'coverage':'full','value':['knowledge']}
        elif phase=='part':
            body=planned['parts'][index];kind='part';n=planned['manifest']['parts'][index][2]
            triples=planned['part_triples'][index]
            items=sorted({o[1] for _,p,o in triples if p==c.ONTOLOGY+'attributedTo'})
            def preflight(call):
                if not items:return True
                values=' '.join('<'+c.iri(s)+'>' for s in items)
                answer=call('/query',{'graph':planes.plane_for('observed'),
                    'query':'SELECT ?item WHERE { VALUES ?item { '+values+' } ?item a ?t . FILTER(?t = <'+c.ONTOLOGY+'WorkItem>) }','verbose':True})
                return answer.get('truncated') is False and {r.get('item') for r in answer.get('rows',[])}==set(items)
            verify=lambda call,_:verify_triples(call,planned['generation'],triples)
        elif phase=='manifest':
            body=planned['metadata'];kind='part';n=0;preflight=None
            verify=lambda call,_:verify_triples(call,planned['generation'],planned['metadata_triples'])
        elif phase=='activate':
            after=unselected_bytes(state)-planned['bytes']+(entry['selected']['bytes'] if entry['selected'] else 0)
            if after>c.STAGED_BYTES:raise ValueError('unselected retention would exceed64MiB')
            body=planned['activation'];kind='marker';n=0;preflight=None
            def verify(call,_):
                checked=read_snapshot(call('/query',snapshot_query(planned['control'],planned['selector'])),
                    producer=policy['producer'],harness=policy['harness'],session=policy['session'],at=planned['manifest']['horizon'])
                return checked['digest']==c.digest(planned['manifest'])
        else:raise ValueError('UNKNOWN: invalid generation phase')
        # Recover cursor-only crash from the durable VERIFIED exact-body proof,
        # never replay an already verified write.
        old_path=state_path.with_suffix('.generation-receipt.json')
        prior=json.loads(old_path.read_text()) if old_path.exists() else {}
        receipt['items_per_snapshot'].append(len({r[8][0] for r in planned['rows'] if r[8]}))
        receipt['records_per_snapshot'].append(n)
        receipt['body_bytes_per_snapshot'].append(byte_size(body))
        body_hash=hashlib.sha256(json.dumps(body,sort_keys=True).encode()).hexdigest()
        recovered=prior.get('status')=='VERIFIED' and prior.get('body_sha256')==body_hash
        if recovered:
            accepted=prior
        else:
            accepted=run_step(kind,body,state_path,now=now,post=post,verify=verify,
                              preflight=preflight,records=n,lane_locked=lane_locked,staged_bytes=unselected_bytes(state))
        progress['receipts'].append({'phase':phase,'index':index,'sha256':body_hash,
                                     'bytes':byte_size(body),'accepted':accepted.get('accepted')})
        if phase=='control':progress['phase']='control-label'
        elif phase=='control-label':progress['phase']='graph'
        elif phase=='graph':progress['phase']='graph-label'
        elif phase=='graph-label':progress['phase']='part' if planned['parts'] else 'manifest'
        elif phase=='part':
            progress['part']+=1
            if progress['part']==len(planned['parts']):progress['phase']='manifest'
        elif phase=='manifest':progress['phase']='activate'
        else:
            if entry['selected']:entry['retained'].append(entry['selected'])
            entry['selected']={'generation':planned['generation'],'source':planned['manifest']['source_sha256'],
                               'producer':policy['producer'],'harness':policy['harness'],'session':policy['session'],
                               'horizon':planned['manifest']['horizon'],'bytes':planned['bytes']}
            entry['progress']=None
        atomic(journal_path,state)
        receipt.update(generation_phase=phase,readback='exact-generation' if phase=='activate' else 'exact-step',
                       recovered_cursor=recovered,knot_seconds=0 if recovered else accepted.get('knot_seconds',0),
                       unknown_reason='none' if phase=='activate' else 'staging')
        return [accepted.get('accepted',{}).get('tx_id')] if not recovered and phase not in ('control','graph','control-label','graph-label') else []
