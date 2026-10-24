"""Exact proposal42/43 RDF and bounded per-session generation plans."""
import json
from urllib.parse import quote

import usage_generation_codec as c

RDF='http://www.w3.org/1999/02/22-rdf-syntax-ns#'
RDFS='http://www.w3.org/2000/01/rdf-schema#'
XSD='http://www.w3.org/2001/XMLSchema#'


def U(value): return ('uri',c.iri(value),None)
def S(value): return ('literal',c.text(value),XSD+'string')
def I(value): return ('literal',str(c.count(value)),XSD+'integer')


def turtle(triples):
    def uri(value):
        value=c.iri(value)
        if value.startswith(c.ONTOLOGY) and value[len(c.ONTOLOGY):].isalnum():
            return 'a:'+value[len(c.ONTOLOGY):]
        return '<'+value+'>'
    def obj(t):
        return uri(t[1]) if t[0]=='uri' else json.dumps(t[1],ensure_ascii=False)+'^^'+uri(t[2])
    subjects={}
    for subject,predicate,term in sorted(set(triples)):
        subjects.setdefault(subject,[]).append((predicate,term))
    lines=['@prefix a: <'+c.ONTOLOGY+'> .']
    for subject,properties in subjects.items():
        lines.append(uri(subject)+' '+' ; '.join((('a' if p==RDF+'type' else uri(p))+' '+obj(t)) for p,t in properties)+' .')
    return '\n'.join(lines)


def session_iri(row): return c.BASE+'session/'+quote(row[0][1],safe='')


def record_triples(row):
    key,tokens,counts,provider,model,model_source,stamp,state,items,actor=row
    subject=c.record_iri(row);session=session_iri(row)
    a=c.ONTOLOGY
    out=[(session,RDF+'type',U(a+'Session')),(session,RDFS+'label',S(key[0]+' session '+key[1])),
         (session,a+'sourceKind',S('observed')),(session,a+'actor',U(c.BASE+'principal/'+quote(actor,safe=''))),
         (subject,RDF+'type',U(a+'UsageRecord')),(subject,RDFS+'label',S(key[2])),
         (subject,a+'sourceKind',S('observed')),(subject,a+'inSession',U(session)),
         (subject,a+'provider',S(provider)),(subject,a+'tokensConsumed',I(int(tokens))),
         (subject,a+'observedAt',S(stamp)),(subject,a+'usageHarness',S(key[0])),
         (subject,a+'usageRequestId',S(key[2])),(subject,a+'usageAttributionState',S(state))]
    for p,v in [('usageModel',model),('usageModelSource',model_source)]:
        if v is not None: out.append((subject,a+p,S(v)))
    for p,v in zip(('inputTokensUncached','cacheReadInputTokens','cacheWriteInputTokens','outputTokens'),counts):
        if v is not None: out.append((subject,a+p,I(int(v))))
    for item in items:
        out.append((subject,a+'attributedTo',U(item)))
        # Graph-local reference stubs are NOT canonical tracker writes. The
        # direct type must be proved in the observed graph before staging.
        # The label states the identifier only, never a guessed tracker title.
        out.extend([(item,RDF+'type',U(a+'WorkItem')),(item,RDFS+'label',S(item.removeprefix(a)))])
    return out


def common(producer,harness,session,at):
    return [('inSession',U(c.BASE+'session/'+quote(session,safe=''))),
            ('usageHarness',S(harness)),('usageProducer',U(producer)),
            ('usageProfileVersion',S(c.VERSION)),('usageHorizon',S(c.horizon(at)))]


def knot(graph,snapshot,triples,actor):
    return {'graph':c.iri(graph),'snapshot':snapshot,'replace_snapshot':True,
            'actor':c.text(actor),'source':'camayoc-cost-generation','turtle':turtle(triples)}


def byte_size(body): return len(json.dumps(body,sort_keys=True).encode())


def plan(records,*,producer,harness,session,at,actor,previous=None):
    rows=c.prepare_rows(records);c.iri(producer);c.text(session);c.horizon(at)
    from work_cost import epoch
    if any(epoch(row[6])>epoch(at) for row in rows):raise ValueError('source record beyond captured horizon')
    if harness not in ('claude','codex') or rows and rows[0][0][:2]!=[harness,session]:
        raise ValueError('session scope mismatch')
    if previous is not None:c.iri(previous)
    placeholder=c.BASE+'generation/'+'0'*64
    chunks=[];current=[]
    def body(i,chunk,graph):
        triples=[t for row in chunk for t in record_triples(row)]
        return knot(graph,'camayoc-cost:'+graph+':'+str(i),triples,actor)
    for row in rows:
        trial=current+[row]
        if len(trial)>c.PART_RECORDS or byte_size(body(len(chunks),trial,placeholder))>c.PART_BYTES:
            if not current:raise ValueError('single request exceeds part budget')
            chunks.append(current);current=[row]
            if byte_size(body(len(chunks),current,placeholder))>c.PART_BYTES:
                raise ValueError('single request exceeds part budget')
        else:current=trial
    if current:chunks.append(current)
    descriptors=[[i,c.digest(chunk),len(chunk),byte_size(body(i,chunk,placeholder))] for i,chunk in enumerate(chunks)]
    manifest={'version':c.VERSION,'producer':producer,'harness':harness,'session':session,
              'horizon':at,'source_sha256':c.digest(rows),'previous':previous,
              'parts':descriptors,'records':len(rows)}
    raw=c.canonical(manifest);md=c.digest(manifest);generation=c.BASE+'generation/'+md
    control=c.BASE+'selector-graph/'+c.digest([producer,harness,session])
    selector=c.BASE+'selector/'+c.digest([producer,harness,session])
    parts=[body(i,chunk,generation) for i,chunk in enumerate(chunks)]
    if [byte_size(b) for b in parts]!=[p[3] for p in descriptors]:raise ValueError('body size identity changed')
    props=common(producer,harness,session,at)
    envelope=[(generation,RDF+'type',U(c.ONTOLOGY+'UsageGeneration'))]
    envelope.extend((generation,c.ONTOLOGY+p,v) for p,v in props)
    envelope.extend((generation,c.ONTOLOGY+p,v) for p,v in [
        ('usageGraph',U(generation)),('usageSourceDigest',S(manifest['source_sha256'])),
        ('usageManifestDigest',S(md)),('usageManifestJSON',S(raw)),
        ('usagePartitionCount',I(len(chunks))),('usageRecordCount',I(len(rows)))])
    if previous:envelope.append((generation,c.ONTOLOGY+'usagePreviousGeneration',U(previous)))
    metadata=knot(generation,'camayoc-cost:'+generation+':manifest',envelope,actor)
    marker=[(selector,RDF+'type',U(c.ONTOLOGY+'UsageGenerationSelector'))]
    marker.extend((selector,c.ONTOLOGY+p,v) for p,v in props)
    marker.extend([(selector,c.ONTOLOGY+'usageSelectedGeneration',U(generation)),
                   (selector,c.ONTOLOGY+'usageManifestDigest',S(md))])
    activation=knot(control,'camayoc-cost:selector:'+selector,marker,actor)
    if byte_size(metadata)>c.PART_BYTES or byte_size(activation)>c.MARKER_BYTES:
        raise ValueError('manifest/marker body budget')
    total=sum(byte_size(b) for b in parts)+byte_size(metadata)
    if total>c.STAGED_BYTES:raise ValueError('generation staged-byte budget')
    return {'manifest':manifest,'generation':generation,'control':control,'selector':selector,
            'parts':parts,'metadata':metadata,'activation':activation,'rows':rows,'bytes':total,
            'part_triples':[[t for row in chunk for t in record_triples(row)] for chunk in chunks],
            'metadata_triples':envelope,'activation_triples':marker}
