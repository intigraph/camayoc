"""Bounded serial native metadata projections per tick; effects never blindly resent.

This library has no automatic enrollment, initial cursor, board or schedule.
The reviewed producer supplies the canonical Reader and durable custody.
"""
from __future__ import annotations
import json
import sqlite3
import time
import seed_change_custody as custody
import seed_metadata_projection as projection
import seed_work_items as native


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


def deliver(reader,journal,call,pending,*,plane,actor,wrote):
    """Prove one pending item and return its version. Raises on any doubt.

    `wrote()` is called BEFORE the one writer request, so a write whose response
    is lost is still counted.
    """
    item,tx,stored=pending
    journal.mark_attempt(item,time.time())
    if stored:
        envelope=json.loads(stored)
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
    if previous and canonical.version==previous[0]:
        if not verify(call,envelope,item):
            raise custody.CustodyUnknown('same native version lost projection; no automatic repair/replay')
        journal.prepare(item,tx,envelope)
    else:
        journal.prepare(item,tx,envelope)
        wrote()
        response=call('/knot',request)
        if (not isinstance(response,dict) or response.get('conforms') is False
            or not response.get('replaced') or type(response.get('tx_id')) is not int):
            raise custody.CustodyUnknown('projection write outcome unproved; preserve exact pending effect')
        if not verify(call,envelope,item):
            raise custody.CustodyUnknown('projection effect not readable; preserve exact pending effect')
    journal.acknowledge(item,tx,envelope['version'])
    return envelope['version']


def tick(reader,journal,post,*,changes,plane,actor,clock=time.monotonic):
    requests=0;writes=0;slowest=0.0
    def wrote():
        nonlocal writes
        writes+=1
    def call(endpoint,body):
        nonlocal requests
        requests+=1
        if requests>TICK_REQUESTS:raise custody.CustodyUnknown('tick request budget exhausted')
        return timed(post,endpoint,body)
    def timed(function,*args,**kwargs):
        nonlocal slowest
        started=clock()
        try:
            return function(*args,**kwargs)
        finally:
            slowest=max(slowest,clock()-started)
    receipt={'status':'UNKNOWN','writes':0,'requests':0,'verified':0}
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
            receipt['status']='UNKNOWN'
            version=deliver(reader,journal,call,pending,plane=plane,actor=actor,wrote=wrote)
            receipt.update(status='OK',verified=receipt['verified']+1,item=pending[0],version=version)
            pending=journal.next()
        if feed_error:
            receipt.update(status='UNKNOWN',feed_error=feed_error)
    except (ValueError,OSError,RuntimeError,TypeError,KeyError,sqlite3.Error) as exc:
        receipt.update(status='UNKNOWN',error=type(exc).__name__)
    if slowest>READ_BUDGET_SECONDS:
        receipt['slow_read']=round(slowest,3)
    receipt.update(requests=requests,writes=writes,cursor=journal.cursor,
                   backlog=journal.db.execute('SELECT COUNT(*) FROM pending').fetchone()[0])
    return receipt
