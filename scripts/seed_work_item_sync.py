"""Bounded serial native metadata projections per tick; effects never blindly resent.

This library has no automatic enrollment, initial cursor, board or schedule.
The reviewed producer supplies the canonical Reader and durable custody.
"""
from __future__ import annotations
import json
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


def verify(post,envelope,item,*,pending=False):
    """Strict readback; a pending effect may distinguish an exact missing type.

    False never verifies an effect. Pending reconciliation must independently
    prove complete subject absence before releasing custody for a fresh write.
    """
    request=envelope['request'];graph=request['graph'];subject=projection.A+item
    # Match the exact type on the server: SELECT bindings may compact its IRI
    # using a deployment-selected prefix. No client alias is type authority.
    control=post('/query',{'query':f'ASK {{ GRAPH <{graph}> {{ <{subject}> a <{projection.A}WorkItem> }} }}'})
    if (not isinstance(control,dict) or control.get('truncated',False) is not False or
        type(control.get('result')) is not bool):
        raise custody.CustodyUnknown('metadata WorkItem control unproved')
    if control['result'] is False:
        if pending:return False
        raise custody.CustodyUnknown('metadata WorkItem control unproved')
    found=post('/query',{'query':f'SELECT ?v WHERE {{ GRAPH <{graph}> {{ <{subject}> <{native.RDFS}comment> ?v }} }} LIMIT 129'})
    rows=found.get('rows') if isinstance(found,dict) else None
    if not isinstance(rows,list) or found.get('truncated') is not False or len(rows)>128:
        raise custody.CustodyUnknown('metadata readback partial')
    expected=envelope['description']
    # One exact attributable current description; unrelated comments need not
    # disappear. They are not authority and are never consumed as native status.
    return sum(native.lexical(row.get('v'))==expected for row in rows)==1


def prove_absent(post,item):
    """True only when the server holds NO triple about the WorkItem subject.

    Both every named graph and the default graph are asked. Anything other than
    an exact, untruncated `false` from both (true, missing, truncated, a
    non-object or an error) is not proof of absence and returns False.
    """
    subject=projection.A+item
    projection.safe_iri(subject)
    for query in (f'ASK {{ GRAPH ?g {{ <{subject}> ?p ?o }} }}',f'ASK {{ <{subject}> ?p ?o }}'):
        try:
            response=post('/query',{'query':query})
        except (ValueError,OSError,RuntimeError,TypeError,KeyError):
            return False
        if (not isinstance(response,dict) or response.get('truncated',False) is not False
                or response.get('result') is not False):
            return False
    return True


#: Serial deliveries per tick. aegis-ksd (wu ACK) set four deliveries and one feed
#: read inside 20 requests. aegis-etv (wu GO 2026-11-04) raised it: that budget
#: capped the lane at 4 seeds transactions/min (MAX_TRANSACTIONS per page, one page
#: per tick) and 240 items/h, while the seeds graph measured 10.0 tx/min and about
#: 307 distinct items/h (10:55-11:45Z). The cursor fell 15 days behind and every
#: item changed after it went unprojected. A tick now reads up to MAX_FEED_PAGES
#: pages and delivers up to MAX_DELIVERIES items (32 tx/min, 720 items/h), all
#: inside TICK_SECONDS of wall clock so it never overlaps the 60 s schedule. The
#: slow-read stop below still degrades a slow store to one item per tick.
MAX_DELIVERIES=12
ITEM_REQUESTS=4
MAX_FEED_PAGES=8
#: Feed pages, one cursor-age read, and the per-item requests.
TICK_REQUESTS=MAX_FEED_PAGES+1+MAX_DELIVERIES*ITEM_REQUESTS
TICK_SECONDS=40.0
#: A request slower than this ends the tick after the item in flight (sattler,
#: aegis-ksd): a slow store degrades to one item per tick instead of exposing
#: four items' verify reads to it. The item in flight always finishes, so the
#: stop itself never leaves an effect indeterminate. The first item is always
#: attempted, so a slow store keeps the old one-item-per-tick rate.
#: 2.5s, not 1.0 (aegis-ksd, sattler 2026-10-16): measured ingress /query mean 0.70s and
#: p90 0.97s, so a 1.0s budget stopped nearly every tick after one item. Incident reads
#: (2-8s) still stop the tick.
READ_BUDGET_SECONDS=2.5


def integer_or_none(value):
    return value if type(value) is int else None


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


def deliver(reader,journal,call,pending,*,plane,actor,wrote):
    """Prove one pending item and return its version. Raises on any doubt.

    `wrote()` is called BEFORE the one writer request, so a write whose response
    is lost is still counted. None means an absent effect was released, not
    verified; its fresh write waits for the next normal tick's request budget.
    """
    item,tx,stored=pending
    journal.mark_attempt(item,time.time())
    if stored:
        envelope=json.loads(stored)
        if verify(call,envelope,item,pending=True):
            journal.acknowledge(item,tx,envelope['version'])
            return envelope['version']
        # A negative readback alone is not absence: partial or conflicting
        # facts keep the exact effect frozen. Reuse the operator release proof.
        if not prove_absent(call,item):
            raise custody.CustodyUnknown('pending effect indeterminate; freeze, no resend')
        journal.release_unconfirmed(item,stored)
        return None
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


def tick(reader,journal,post,*,changes,plane,actor,clock=time.monotonic,
         cursor_time=None,wall=time.time):
    requests=0;writes=0;slowest=0.0;last=None
    tick_started=clock()
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
    # Set only by a page that proves the lane holds nothing newer for this
    # graph: an empty valid page, or one read through the watermark.
    caught_up=False
    try:
        pending=journal.next()
        feed_error=None
        backlog=journal.db.execute('SELECT COUNT(*) FROM pending').fetchone()[0]
        if backlog < custody.MAX_PENDING and changes is not None:
            # Up to MAX_FEED_PAGES pages (aegis-etv). The first page keeps the
            # original contract exactly. A later page stops feeding quietly when
            # custody is full, the feed is caught up, or half the tick's wall clock
            # is spent; the retained pages and the cursor they advanced stand.
            for page_number in range(MAX_FEED_PAGES):
                if page_number and (backlog>=custody.MAX_PENDING
                                    or clock()-tick_started>TICK_SECONDS/2):
                    break
                requests+=1
                before=journal.cursor
                try:
                    caught_up=False
                    page=timed(changes,since=journal.cursor,limit=custody.MAX_TRANSACTIONS,
                                 capture='new_values',graph=reader.board.graph)
                    journal.retain(page)
                    # The feed is graph-filtered while watermark_tx is global, so a
                    # quiet board answers an empty page with next_tx == since, far
                    # below the watermark. That empty page is the caught-up proof.
                    caught_up=(isinstance(page,dict) and (page.get('records')==[] or
                               integer_or_none(page.get('next_tx'))==integer_or_none(page.get('watermark_tx'))))
                except custody.CustodyFull:
                    if page_number:break
                    if pending is None:raise
                    feed_error='CustodyFull';break
                except (ValueError,OSError,RuntimeError,TypeError,KeyError,sqlite3.Error) as exc:
                    if page_number==0 and pending is None:raise
                    feed_error=type(exc).__name__;break
                backlog=journal.db.execute('SELECT COUNT(*) FROM pending').fetchone()[0]
                if (journal.cursor==before or not isinstance(page,dict)
                        or integer_or_none(page.get('next_tx'))==integer_or_none(page.get('watermark_tx'))):
                    break
            pending=journal.next()
        elif backlog >= custody.MAX_PENDING:
            feed_error='PendingCapacityReached'
        receipt['status']='OK'
        seen=set()
        # Serial: the first doubt stops the tick; earlier items stay acknowledged.
        while (pending is not None and len(seen)<MAX_DELIVERIES
               and pending[0] not in seen and requests+ITEM_REQUESTS<=TICK_REQUESTS
               and (not seen or slowest<=READ_BUDGET_SECONDS)
               and (not seen or clock()-tick_started<=TICK_SECONDS)):
            seen.add(pending[0])
            # `item` remains the last verified success for compatibility.
            # Attribute this attempt separately, even if its control fails.
            receipt.pop('attempted_item',None)
            receipt.pop('control',None)
            last=None
            # Diagnostic validation must not alter delivery or custody decisions.
            try:reader.board.subject(pending[0])
            except native.NativeUnknown:pass
            else:receipt['attempted_item']=pending[0]
            receipt['status']='UNKNOWN'
            version=deliver(reader,journal,call,pending,plane=plane,actor=actor,wrote=wrote)
            if version is None:
                receipt.update(status='OK',reconciled=receipt.get('reconciled',0)+1)
            else:
                receipt.update(status='OK',verified=receipt['verified']+1,item=pending[0],version=version)
            pending=journal.next()
        if feed_error:
            receipt.update(status='UNKNOWN',feed_error=feed_error)
    except (ValueError,OSError,RuntimeError,TypeError,KeyError,sqlite3.Error) as exc:
        receipt.update(status='UNKNOWN',error=type(exc).__name__)
        reason=failure_reason(exc)
        if reason:receipt['reason']=reason
        if last:receipt['last_request']=last
    # Cursor age (aegis-etv): how far behind the feed position is, in wall clock.
    # Exit status and backlog cannot see this: a lane 15 days behind read OK.
    # A failed read omits the field (UNKNOWN to the alarm), never guesses 0.
    # Caught up: no board record is newer than the cursor, so the lane is not
    # behind however old the cursor transaction is (a quiet board). Reported
    # as 0 only on that proof; a feed error never yields 0.
    if cursor_time is not None and caught_up and not feed_error and 'error' not in receipt:
        receipt['cursor_age_seconds']=0
    elif cursor_time is not None and requests<TICK_REQUESTS:
        requests+=1
        try:
            stamp=cursor_time(journal.cursor)
            if isinstance(stamp,(int,float)) and not isinstance(stamp,bool):
                receipt['cursor_age_seconds']=max(0,round(wall()-stamp))
            else:
                receipt['cursor_age_error']='unproved'
        except (ValueError,OSError,RuntimeError,TypeError,KeyError) as exc:
            receipt['cursor_age_error']=type(exc).__name__
    if slowest>READ_BUDGET_SECONDS:
        receipt['slow_read']=round(slowest,3)
    receipt['slowest_read']=round(slowest,3)
    receipt.update(requests=requests,writes=writes,cursor=journal.cursor,
                   backlog=journal.db.execute('SELECT COUNT(*) FROM pending').fetchone()[0])
    return receipt
