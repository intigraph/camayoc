"""Bounded serial native metadata projections per tick; effects never blindly resent.

This library has no automatic enrollment, initial cursor, board or schedule.
The reviewed producer supplies the canonical Reader and durable custody.
"""
from __future__ import annotations
import json
import hashlib
import re
import sqlite3
import time
import seed_change_custody as custody
import seed_metadata_projection as projection
import seed_work_items as native


def control_summary(response):
    """Fixed classifications only: never response bytes, values, auth or URLs."""
    if not isinstance(response,dict):
        return {'shape':'array' if isinstance(response,list) else 'other',
                'result':'missing','truncated':'absent'}
    def marker(key):
        if key not in response:return 'absent'
        if response[key] is True:return 'true'
        if response[key] is False:return 'false'
        return 'nonboolean'
    result=marker('result')
    return {'shape':'object','result':'missing' if result=='absent' else result,
            'truncated':marker('truncated')}


def verify(post,envelope,item):
    request=envelope['request'];graph=request['graph'];subject=projection.A+item
    # Match the exact type on the server: SELECT bindings may compact its IRI
    # using a deployment-selected prefix. No client alias is type authority.
    control=post('/query',{'query':f'ASK {{ GRAPH <{graph}> {{ <{subject}> a <{projection.A}WorkItem> }} }}'})
    if (not isinstance(control,dict) or control.get('truncated') or
        control.get('result') is not True):
        raise custody.CustodyUnknown('metadata WorkItem control unproved')
    found=post('/query',{'query':f'SELECT ?v WHERE {{ GRAPH <{graph}> {{ <{subject}> <{native.RDFS}comment> ?v }} }} LIMIT 129'})
    rows=found.get('rows') if isinstance(found,dict) else None
    if not isinstance(rows,list) or found.get('truncated') or len(rows)>128:
        raise custody.CustodyUnknown('metadata readback partial')
    expected=envelope['description']
    # One exact attributable current description; unrelated comments need not
    # disappear. They are not authority and are never consumed as native status.
    return sum(native.lexical(row.get('v'))==expected for row in rows)==1


#: Serial deliveries per tick (wu ACK, aegis-ksd): one feed read plus at most four
#: requests per item (native read, /knot, two-pattern verify) stays within 20.
MAX_DELIVERIES=4
ITEM_REQUESTS=4
TICK_REQUESTS=1+MAX_DELIVERIES*ITEM_REQUESTS
#: A request slower than this ends the tick after the item in flight (sattler,
#: aegis-ksd): a slow store degrades to one item per tick instead of exposing
#: four items' verify reads to it. The item in flight always finishes, so the
#: stop itself never leaves an effect indeterminate. The first item is always
#: attempted, so a slow store keeps the old one-item-per-tick rate.
#: 2.5s, not 1.0 (aegis-ksd, sattler 2026-10-16): measured ingress /query mean 0.70s and
#: p90 0.97s, so a 1.0s budget stopped nearly every tick after one item. Incident reads
#: (2-8s) still stop the tick.
READ_BUDGET_SECONDS=2.5


def failure_reason(exc):
    """A bounded, payload-free reason for a failed tick (aegis-wu1s9ti4d).

    CustodyUnknown carries only this library's literal reasons. A PlaneError is
    reduced to a fixed class, because its text can hold server response bytes.
    Anything else keeps only its type, as before. Never auth, payloads or URLs.
    """
    if isinstance(exc,custody.CustodyUnknown):return str(exc)[:120]
    if type(exc).__name__!='PlaneError':return None
    text=str(exc);code=re.search(r'\bHTTP (\d{3})\b',text)
    if code:return 'HTTP '+code.group(1)
    for marker,name in (('unreachable','unreachable'),('budget','response budget'),('credential','auth refused'),('401','auth refused')):
        if marker in text:return name
    return 'unclassified'


def envelope_witness(witness,request):
    """Diagnostic only; hash the JSON bytes used by planes._post, never retain them."""
    if witness is None:return
    try:
        graph=request['graph']
        projection.safe_iri(graph)
        if len(graph)<=2048 and all(32<ord(c)<127 for c in graph):
            witness['graph']=graph
    except (KeyError,TypeError,ValueError,native.NativeUnknown):pass
    try:
        witness['body_sha256']=hashlib.sha256(json.dumps(request,sort_keys=True).encode()).hexdigest()
    except (TypeError,ValueError,UnicodeError):pass


def deliver(reader,journal,call,pending,*,plane,actor,wrote,witness=None):
    """Prove one pending item and return its version. Raises on any doubt.

    `wrote()` is called BEFORE the one writer request, so a write whose response
    is lost is still counted.
    """
    item,tx,stored=pending
    journal.mark_attempt(item,time.time())
    if stored:
        if witness is not None:witness['branch']='stored-readback'
        envelope=json.loads(stored)
        envelope_witness(witness,envelope['request'])
        if not verify(call,envelope,item):
            raise custody.CustodyUnknown('pending effect indeterminate; freeze, no resend')
        journal.acknowledge(item,tx,envelope['version'])
        return envelope['version']
    # Reader's own request gets the same total budget accounting.
    canonical=native.Reader(call,reader.board).get(item)
    previous=journal.db.execute('SELECT version FROM verified WHERE id=?',(item,)).fetchone()
    if previous and canonical.version<previous[0]:
        raise custody.CustodyUnknown('native source version regressed')
    request=projection.body(canonical,plane=plane,actor=actor)
    description=projection.description(canonical)
    envelope={'request':request,'version':canonical.version,'description':description}
    envelope_witness(witness,request)
    if previous and canonical.version==previous[0]:
        if witness is not None:witness['branch']='same-version'
        if not verify(call,envelope,item):
            raise custody.CustodyUnknown('same native version lost projection; no automatic repair/replay')
        journal.prepare(item,tx,envelope)
    else:
        if witness is not None:witness['branch']='new-write'
        journal.prepare(item,tx,envelope)
        wrote()
        if witness is not None:witness['own_write']='attempted'
        response=call('/knot',request)
        if (not isinstance(response,dict) or response.get('conforms') is False
            or not response.get('replaced') or type(response.get('tx_id')) is not int):
            raise custody.CustodyUnknown('projection write outcome unproved; preserve exact pending effect')
        if witness is not None:witness['own_write']='proved'
        if not verify(call,envelope,item):
            raise custody.CustodyUnknown('projection effect not readable; preserve exact pending effect')
    journal.acknowledge(item,tx,envelope['version'])
    return envelope['version']


def tick(reader,journal,post,*,changes,plane,actor,clock=time.monotonic):
    requests=0;writes=0;slowest=0.0;last=None
    def wrote():
        nonlocal writes
        writes+=1
    def call(endpoint,body):
        nonlocal requests
        requests+=1
        if requests>TICK_REQUESTS:raise custody.CustodyUnknown('tick request budget exhausted')
        response=timed(post,endpoint,body)
        if endpoint=='/query' and body.get('query','').startswith('ASK '):
            receipt['control']=control_summary(response)
        return response
    def timed(function,*args,**kwargs):
        nonlocal slowest,last
        started=clock()
        try:
            return function(*args,**kwargs)
        finally:
            elapsed=clock()-started
            slowest=max(slowest,elapsed)
            # The request whose answer a doubt rests on: for a failed custody
            # read-back this is the read-back itself (aegis-wu1s9ti4d).
            last={'endpoint':args[0] if args else '/changes','seconds':round(elapsed,3)}
    receipt={'status':'UNKNOWN','writes':0,'requests':0,'verified':0}
    def reset_witness():
        receipt['envelope_witness']={'schema':'native-envelope-witness-v1',
            'body_encoding':'planes-json-sort-keys-v1','graph':'UNKNOWN',
            'body_sha256':'UNKNOWN','branch':'UNKNOWN','own_write':'not-attempted'}
    reset_witness()
    try:
        pending=journal.next()
        feed_error=None
        backlog=journal.db.execute('SELECT COUNT(*) FROM pending').fetchone()[0]
        if backlog < custody.MAX_PENDING and changes is not None:
            requests+=1
            try:
                page=timed(changes,since=journal.cursor,limit=custody.MAX_TRANSACTIONS,
                             capture='new_values',graph=reader.board.graph)
                journal.retain(page)
            except (ValueError,OSError,RuntimeError,TypeError,KeyError,sqlite3.Error) as exc:
                if pending is None:raise
                feed_error=type(exc).__name__
            pending=journal.next()
        elif backlog >= custody.MAX_PENDING:
            feed_error='PendingCapacityReached'
        receipt['status']='OK'
        seen=set()
        # Serial: the first doubt stops the tick; earlier items stay acknowledged.
        while (pending is not None and len(seen)<MAX_DELIVERIES
               and pending[0] not in seen and requests+ITEM_REQUESTS<=TICK_REQUESTS
               and (not seen or slowest<=READ_BUDGET_SECONDS)):
            seen.add(pending[0])
            # `item` remains the last verified success for compatibility.
            # Attribute this attempt separately, even if its control fails.
            receipt.pop('attempted_item',None)
            receipt.pop('control',None)
            reset_witness()
            last=None
            # Diagnostic validation must not alter delivery or custody decisions.
            try:reader.board.subject(pending[0])
            except native.NativeUnknown:pass
            else:receipt['attempted_item']=pending[0]
            receipt['status']='UNKNOWN'
            version=deliver(reader,journal,call,pending,plane=plane,actor=actor,wrote=wrote,
                            witness=receipt['envelope_witness'] if 'attempted_item' in receipt else None)
            receipt.update(status='OK',verified=receipt['verified']+1,item=pending[0],version=version)
            pending=journal.next()
        if feed_error:
            receipt.update(status='UNKNOWN',feed_error=feed_error)
    except (ValueError,OSError,RuntimeError,TypeError,KeyError,sqlite3.Error) as exc:
        receipt.update(status='UNKNOWN',error=type(exc).__name__)
        reason=failure_reason(exc)
        if reason:receipt['reason']=reason
        if last:receipt['last_request']=last
    if slowest>READ_BUDGET_SECONDS:
        receipt['slow_read']=round(slowest,3)
    receipt['slowest_read']=round(slowest,3)
    receipt.update(requests=requests,writes=writes,cursor=journal.cursor,
                   backlog=journal.db.execute('SELECT COUNT(*) FROM pending').fetchone()[0])
    return receipt
