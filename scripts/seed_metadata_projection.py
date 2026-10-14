"""Current native metadata in one source-owned atomic snapshot.

No Observation, verification date, expiry policy, or legacy owner/status is
fabricated. The same exact WorkItem identity is reused. Snapshot replacement
owns only this producer's claims in the reviewed knowledge plane.
"""
from __future__ import annotations
import hashlib
import json
import seed_work_items as nw

A='http://aegis.gastown.local/ontology/'


def literal(value):
    return json.dumps(value,ensure_ascii=False)


def safe_iri(value):
    if not isinstance(value,str) or not value.startswith(('http://','https://','urn:')) or any(c in value for c in '<>"{}\\\n\r\t '):
        raise nw.NativeUnknown('projection destination is not a safe explicit IRI')
    return '<'+value+'>'


def description(record:nw.Record) -> str:
    return json.dumps({'id':record.id,'title':record.title,'description':record.description,
                     'labels':record.labels,'kind':record.kind,'native_action':record.action,
                     'source_graph':record.graph,'source_version':record.version},
                    ensure_ascii=False,sort_keys=True,separators=(',',':'))


def body(record:nw.Record,*,plane:str,actor:str):
    subject=A+record.id
    safe_iri(plane);safe_iri(subject)
    if not actor or len(actor)>128:raise nw.NativeUnknown('projection actor missing or too long')
    # An attributable description makes title/body/labels searchable on the
    # WorkItem itself. The native Action remains status/owner/date authority.
    text=description(record)
    triples=[(nw.RDF+'type',safe_iri(A+'WorkItem')),
             (nw.RDFS+'label',literal(record.id)),(nw.RDFS+'comment',literal(text)),
             (A+'sourceKind',literal('observed')),(A+'identifier',literal(record.id)),
             (nw.SCHEMA+'name',literal(record.title)),
             (nw.SCHEMA+'description',literal(record.description)),
             (nw.SCHEMA+'version',literal(str(record.version))+'^^<http://www.w3.org/2001/XMLSchema#integer>'),
             (nw.SCHEMA+'dateModified',literal(record.modified)+'^^<http://www.w3.org/2001/XMLSchema#dateTime>'),
             (A+'about',safe_iri(record.action))]
    triples.extend((nw.SCHEMA+'keywords',literal(label)) for label in record.labels)
    triples.extend((A+'about',safe_iri(target)) for target in record.about)
    turtle='\n'.join(f'{safe_iri(subject)} {safe_iri(predicate)} {value} .' for predicate,value in triples)
    identity=hashlib.sha256((record.graph+'\0'+record.id).encode()).hexdigest()
    result={'turtle':turtle,'graph':plane,'snapshot':'camayoc-seeds-metadata:'+identity,
            'replace_snapshot':True,'actor':actor,'source':record.action}
    if len(json.dumps(result).encode())>nw.MAX_BYTES:raise nw.NativeUnknown('metadata projection exceeds write budget')
    return result
