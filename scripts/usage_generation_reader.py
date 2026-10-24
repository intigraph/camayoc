"""One-response complete-set validation. Errors are UNKNOWN, never partial cost."""
import re
from urllib.parse import quote,unquote

import usage_generation_codec as c
from usage_generation_terms import parse_response
from usage_generation_wire import RDF,RDFS,XSD,U,S,I


class Facts:
    def __init__(self,triples):
        self.values={};self.used=set()
        for s,p,o in triples:self.values.setdefault((s,p),[]).append(o)

    def one(self,s,p,kind='literal',datatype=XSD+'string',optional=False):
        key=(s,p);values=self.values.get(key,[])
        if optional and not values:return None
        if len(values)!=1:raise ValueError('UNKNOWN: scalar cardinality')
        term=values[0]
        if term[0]!=kind or term[2]!=(None if kind=='uri' else datatype):
            raise ValueError('UNKNOWN: wrong scalar RDF kind/datatype')
        self.used.add(key);return term[1]

    def exact(self,s,p,value):
        got=self.one(s,p,value[0],value[2])
        if got!=value[1]:raise ValueError('UNKNOWN: fixed fact differs')

    def done(self):
        if set(self.values)!=self.used:raise ValueError('UNKNOWN: unexpected facts')


def read_snapshot(response,*,producer,harness,session,at):
    control=c.BASE+'selector-graph/'+c.digest([producer,harness,session])
    selector=c.BASE+'selector/'+c.digest([producer,harness,session])
    quads=parse_response(response,control)
    cf=Facts([(s,p,o) for g,s,p,o in quads if g==control]);a=c.ONTOLOGY
    cf.exact(selector,RDF+'type',U(a+'UsageGenerationSelector'))
    for p,t in [('inSession',U(c.BASE+'session/'+quote(session,safe=''))),
                ('usageHarness',S(harness)),('usageProducer',U(producer)),
                ('usageProfileVersion',S(c.VERSION)),('usageHorizon',S(c.horizon(at)))]:
        cf.exact(selector,a+p,t)
    gen=cf.one(selector,a+'usageSelectedGeneration','uri')
    selected_digest=cf.one(selector,a+'usageManifestDigest');cf.done()
    if not c.HEX.fullmatch(selected_digest) or gen!=c.BASE+'generation/'+selected_digest:
        raise ValueError('UNKNOWN: selector digest/identity mismatch')
    if {g for g,*_ in quads}!={control,gen}:
        raise ValueError('UNKNOWN: mixed/unselected/legacy graph')
    gf=Facts([(s,p,o) for g,s,p,o in quads if g==gen])
    gf.exact(gen,RDF+'type',U(a+'UsageGeneration'))
    for p,t in [('inSession',U(c.BASE+'session/'+quote(session,safe=''))),
                ('usageHarness',S(harness)),('usageProducer',U(producer)),
                ('usageProfileVersion',S(c.VERSION)),('usageHorizon',S(at)),('usageGraph',U(gen))]:
        gf.exact(gen,a+p,t)
    raw=gf.one(gen,a+'usageManifestJSON')
    m=c.validate_manifest(raw,producer=producer,harness=harness,session=session,at=at)
    if c.digest(m)!=selected_digest:raise ValueError('UNKNOWN: manifest digest differs')
    gf.exact(gen,a+'usageManifestDigest',S(selected_digest))
    gf.exact(gen,a+'usageSourceDigest',S(m['source_sha256']))
    gf.exact(gen,a+'usagePartitionCount',I(len(m['parts'])))
    gf.exact(gen,a+'usageRecordCount',I(m['records']))
    previous=gf.one(gen,a+'usagePreviousGeneration','uri',optional=True)
    if previous!=m['previous']:raise ValueError('UNKNOWN: correction lineage mismatch')
    subjects={s for s,p in gf.values if p==RDF+'type' and U(a+'UsageRecord') in gf.values[(s,p)]}
    rows=[];sessions=set();items=set()
    for s in subjects:
        gf.exact(s,RDF+'type',U(a+'UsageRecord'));gf.exact(s,a+'sourceKind',S('observed'))
        gf.one(s,RDFS+'label')
        si=gf.one(s,a+'inSession','uri')
        if si!=c.BASE+'session/'+quote(session,safe=''):raise ValueError('UNKNOWN: mixed session')
        sessions.add(si);gf.exact(s,a+'usageHarness',S(harness))
        request=gf.one(s,a+'usageRequestId');c.text(request)
        provider=gf.one(s,a+'provider')
        if provider!=('openai' if harness=='codex' else 'anthropic'):raise ValueError('UNKNOWN: provider')
        def number(p,optional=False):
            v=gf.one(s,a+p,datatype=XSD+'integer',optional=optional)
            if v is not None and not re.fullmatch(r'0|[1-9][0-9]*',v):raise ValueError('UNKNOWN: noncanonical count')
            return v
        tokens=number('tokensConsumed')
        counts=[number(p,True) for p in ('inputTokensUncached','cacheReadInputTokens','cacheWriteInputTokens','outputTokens')]
        model=gf.one(s,a+'usageModel',optional=True);ms=gf.one(s,a+'usageModelSource',optional=True)
        stamp=gf.one(s,a+'observedAt');state=gf.one(s,a+'usageAttributionState')
        item=gf.one(s,a+'attributedTo','uri',optional=True)
        if (state not in ('attributed','no proven focus','overlapping focus')
                or (state=='attributed')!=(item is not None)):
            raise ValueError('UNKNOWN: contradictory attribution')
        if item:items.add(item)
        actor_iri=gf.one(si,a+'actor','uri')
        if not actor_iri.startswith(c.BASE+'principal/'):raise ValueError('UNKNOWN: actor route')
        actor=unquote(actor_iri.removeprefix(c.BASE+'principal/'))
        if c.BASE+'principal/'+quote(actor,safe='')!=actor_iri:raise ValueError('UNKNOWN: actor encoding')
        row=[[harness,session,request],tokens,counts,provider,model,ms,stamp,state,[item] if item else [],actor]
        if s!=c.record_iri(row):raise ValueError('UNKNOWN: request identity differs')
        rows.append(row)
    for si in sessions:
        gf.exact(si,RDF+'type',U(a+'Session'));gf.exact(si,a+'sourceKind',S('observed'))
        gf.exact(si,RDFS+'label',S(harness+' session '+session))
    for item in items:
        if not item.startswith(a):raise ValueError('UNKNOWN: WorkItem namespace')
        gf.exact(item,RDF+'type',U(a+'WorkItem'))
        gf.exact(item,RDFS+'label',S(item.removeprefix(a)))
    gf.done();rows.sort(key=lambda r:r[0])
    if len(rows)!=m['records'] or len({tuple(r[0]) for r in rows})!=len(rows) or c.digest(rows)!=m['source_sha256']:
        raise ValueError('UNKNOWN: record set/source digest differs')
    index=0
    for _,sha,n,_bytes in m['parts']:
        if c.digest(rows[index:index+n])!=sha:raise ValueError('UNKNOWN: missing/tampered/mixed partition')
        index+=n
    return {'status':'COMPLETE','manifest':m,'rows':rows,'generation':gen,'digest':selected_digest,
            'scope':'one supplied session snapshot; source-log/global completeness not asserted'}


def consume(*,producer,harness,session,at,state_path,now,post=None):
    """Read-only consumer build, one graph-bound response per5min; no polling."""
    import json
    from pathlib import Path
    import planes
    from usage_generation_budget import admit_read
    from usage_generation_step import locked,atomic
    from usage_generation_terms import snapshot_query
    c.iri(producer);c.text(session);c.horizon(at)
    if harness not in ('claude','codex'):raise ValueError('UNKNOWN: invalid reader harness')
    state_path=Path(state_path)
    control=c.BASE+'selector-graph/'+c.digest([producer,harness,session])
    selector=c.BASE+'selector/'+c.digest([producer,harness,session])
    transport=post or planes._post
    with locked(state_path):
        state=json.loads(state_path.read_text()) if state_path.exists() else {'attempt':None,'complete':None}
        if not isinstance(state,dict) or set(state)!={'attempt','complete'}:
            raise ValueError('UNKNOWN: invalid consumer state')
        try:attempt=admit_read(now=now,previous=state['attempt'],graph=control)
        except ValueError:
            return {'status':'UNKNOWN','reason':'consumer_cadence_or_clock','last_complete':state['complete']}
        # Failed attempts count too; never poll after an error or lower the cap.
        state['attempt']=attempt;atomic(state_path,state)
        try:
            response=transport('/query',snapshot_query(control,selector),client='camayoc-cost-generation',response_limit=c.READ_BYTES)
            checked=read_snapshot(response,producer=producer,harness=harness,session=session,at=at)
        except (ValueError,OSError,planes.PlaneError):
            return {'status':'UNKNOWN','reason':'generation_validation','last_complete':state['complete']}
        state['complete']=checked;atomic(state_path,state)
        return checked
