"""Bounded serial native metadata projections per tick; effects never blindly resent.

This library has no automatic enrollment, initial cursor, board or schedule.
The reviewed producer supplies the canonical Reader and durable custody.
"""
from __future__ import annotations
import json
import hashlib
import re
import secrets
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
    prove native completion and exact pending-effect absence before release.
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



def verify_fused(post,envelope,item,*,diagnostics=None):
    """One bounded same-version proof; never used for writes or pending effects.

    Native 1792 projects BIND(true) as a JSON boolean. EXISTS in BIND is
    unbound there, so use the exact mandatory type inside its own OPTIONAL.
    Missing or ambiguous witnesses remain UNKNOWN, never an absence proof.
    """
    graph=projection.safe_iri(envelope['request']['graph'])
    subject=projection.safe_iri(projection.A+item)
    kind=projection.safe_iri(projection.A+'WorkItem')
    query=(f'SELECT ?has_type ?v WHERE {{ GRAPH {graph} {{ '
           f'OPTIONAL {{ {subject} a {kind} . BIND(true AS ?has_type) }} '
           f'OPTIONAL {{ {subject} <{native.RDFS}comment> ?v }} }} }} LIMIT 129')
    found=post('/query',{'query':query})
    # This is explicitly SELECT evidence, not a synthetic ASK response. Old
    # control_summary and every actual ASK receipt remain unchanged.
    diagnostic={'schema':'workitem-fused-readback/v1',**control_summary(found),
                'type_witness':'unproved','description':'unproved'}
    if diagnostics is not None:diagnostics['fused_control']=diagnostic
    try:
        size=len(json.dumps(found,ensure_ascii=False,separators=(',',':')).encode('utf-8'))
    except (TypeError,ValueError,UnicodeError):
        raise custody.CustodyUnknown('fused readback encoding unproved') from None
    rows=found.get('rows') if isinstance(found,dict) else None
    if (size>262144 or not isinstance(rows,list) or found.get('truncated') is not False
            or not 1<=len(rows)<=128):
        raise custody.CustodyUnknown('fused readback partial or outside budget')
    if 'count' in found and (type(found['count']) is not int or found['count']!=len(rows)):
        raise custody.CustodyUnknown('fused readback cardinality unproved')
    if 'variables' in found and found['variables']!=['has_type','v']:
        raise custody.CustodyUnknown('fused readback bindings unproved')
    comments=[]
    for row in rows:
        if (not isinstance(row,dict) or not set(row)<= {'has_type','v'}
                or row.get('has_type') is not True):
            raise custody.CustodyUnknown('fused WorkItem type witness unproved')
        if 'v' not in row:
            if len(rows)!=1:raise custody.CustodyUnknown('fused sentinel mixed with comments')
            continue
        try:comments.append(native.lexical(row['v']))
        except (ValueError,TypeError):
            raise custody.CustodyUnknown('fused comment representation unproved') from None
    diagnostic['type_witness']='true'
    matches=comments.count(envelope['description'])
    diagnostic['description']='one' if matches==1 else 'missing' if matches==0 else 'multiple'
    return matches==1

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


def completion_summary(status, write_id):
    """Fixed, payload-free observations; absent/evicted are claims, not proofs.

    Never retain arbitrary state strings, receipt values, request IDs or bodies.
    This summary does not participate in any delivery or release decision.
    """
    obj = status if isinstance(status, dict) else {}
    state = obj.get('state')
    receipt = obj.get('receipt')
    receipt = receipt if isinstance(receipt, dict) else {}
    truncated = control_summary(status)['truncated']
    return {'schema': 'workitem-completion-disposition/v1',
            'response': 'object' if isinstance(status, dict) else 'nonobject',
            'contract_match': obj.get('contract') == 'quipu.knot-completion/v1',
            'id_match': obj.get('write_id') == write_id,
            'truncated': truncated,
            'state': state if isinstance(state, str) and state in (
                'not_started', 'stopped', 'running', 'finished', 'unknown', 'absent', 'evicted')
                else 'unrecognized' if 'state' in obj else 'missing',
            'receipt_conforms': receipt.get('conforms') is True,
            'receipt_replaced': receipt.get('replaced') is True,
            'receipt_tx_valid': type(receipt.get('tx_id')) is int and receipt['tx_id'] >= 0,
            'absence': 'not-attempted'}


def completed_without_effect(post, journal, envelope, item, *, diagnostics=None):
    """A retained native fence plus exact effect absence, never elapsed time.

    Old journals, evicted receipts and unavailable capabilities stay UNKNOWN.
    A stopped worker may have committed: readback must still prove absence.
    """
    summary = {'schema': 'workitem-completion-disposition/v1',
               'response': 'not-attempted', 'absence': 'not-attempted'}
    if diagnostics is not None:
        diagnostics['completion_disposition'] = summary
    write_id = envelope.get('write_id')
    if not isinstance(write_id, str) or not re.fullmatch('[0-9a-f]{64}', write_id):
        return False
    try:
        status = post('/knot/status', {'write_id': write_id, 'request': envelope['request']})
    except (ValueError, OSError, RuntimeError, TypeError, KeyError) as exc:
        summary['response'] = ('refused' if type(exc).__name__ == 'PlaneError' and
                               failure_reason(exc) in ('HTTP 401', 'HTTP 403', 'auth refused')
                               else 'unavailable')
        raise
    summary.update(completion_summary(status, write_id))
    if (not isinstance(status, dict) or status.get('contract') != 'quipu.knot-completion/v1'
            or status.get('write_id') != write_id or status.get('truncated', False) is not False
            or status.get('state') not in ('not_started', 'stopped')):
        return False
    previous = journal.db.execute('SELECT version FROM verified WHERE id=?', (item,)).fetchone()
    if previous is None:
        absent = prove_absent(post, item)
        summary['absence'] = 'proved' if absent else 'unproved'
        return absent
    if type(envelope.get('version')) is not int or envelope['version'] <= previous[0]:
        return False
    summary['absence'] = 'unproved'
    # An update does not erase its previously verified snapshot on failure.
    # Permit that exact older version's canonical metadata, not arbitrary facts
    # or a conflicting/newer version, and never require the entire subject gone.
    graph = projection.safe_iri(envelope['request']['graph'])
    subject = projection.safe_iri(projection.A + item)
    found = post('/query', {'query': f'SELECT ?version ?v ?datatype WHERE {{ GRAPH {graph} {{ {subject} a <{projection.A}WorkItem> ; <{native.SCHEMA}version> ?version ; <{native.RDFS}comment> ?v }} BIND(IF(DATATYPE(?version) = <http://www.w3.org/2001/XMLSchema#integer>, \"integer\", \"other\") AS ?datatype) }} LIMIT 129'})
    if (not isinstance(found, dict) or found.get('truncated') is not False
            or not isinstance(found.get('rows'), list) or len(found['rows']) != 1):
        return False
    row = found['rows'][0]
    if not isinstance(row, dict) or set(row) != {'version', 'v', 'datatype'}:
        return False
    try:
        if (native.lexical(row.get('version')) != str(previous[0])
                or native.lexical(row.get('datatype')) != 'integer'):
            return False
        prior = json.loads(native.lexical(row.get('v')))
        expected = json.loads(envelope['description'])
    except (ValueError, TypeError, AttributeError):
        return False
    absent = (isinstance(prior, dict) and set(prior) == set(expected)
            and type(prior.get('source_version')) is int
            and prior['source_version'] == previous[0]
            and all(prior.get(k) == expected.get(k) for k in ('id', 'source_graph', 'native_action')))
    summary['absence'] = 'proved' if absent else 'unproved'
    return absent


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
    for marker,name in (('gave no answer','no answer; outcome unknown'),('unreachable','unreachable'),('budget','response budget'),('credential','auth refused'),('401','auth refused')):
        if marker in text:return name
    return 'unclassified'


def response_lost(exc):
    """Transport uncertainty only; authorization and application refusals stay put."""
    return (isinstance(exc,OSError) or
            (type(exc).__name__=='PlaneError' and
             ('gave no answer' in str(exc) or 'unreachable' in str(exc))))


def finished_after_response_loss(call,envelope):
    """One bounded authenticated lookup, bound to the persisted original request.

    Native completion is process-local. Missing, evicted or incomplete evidence
    never authorizes another write. A finished receipt still needs effect readback.
    """
    try:
        status=call('/knot/status',{'write_id':envelope['write_id'],
                                   'request':envelope['request']},response_limit=4096)
        size=len(json.dumps(status,ensure_ascii=False,separators=(',',':')).encode())
    except (ValueError,OSError,RuntimeError,TypeError,KeyError,UnicodeError):
        raise custody.CustodyUnknown('knot completion unavailable; freeze, no resend') from None
    if (size>4096 or not isinstance(status,dict)
            or status.get('contract')!='quipu.knot-completion/v1'
            or status.get('write_id')!=envelope['write_id']
            or status.get('truncated',False) is not False
            or status.get('state')!='finished'):
        raise custody.CustodyUnknown('knot completion indeterminate; freeze, no resend')
    receipt=status.get('receipt')
    if (not isinstance(receipt,dict) or receipt.get('conforms') is not True
            or receipt.get('replaced') is not True or type(receipt.get('tx_id')) is not int
            or receipt['tx_id']<0):
        raise custody.CustodyUnknown('knot completion receipt unproved; freeze, no resend')
    return receipt


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


def deliver(reader,journal,call,pending,*,plane,actor,wrote,completion=False,witness=None,diagnostics=None):
    """Prove one pending item and return its version. Raises on any doubt.

    `wrote()` is called BEFORE the one writer request, so a write whose response
    is lost is still counted. None means an absent effect was released, not
    verified; its fresh write waits for the next normal tick's request budget.
    """
    item,tx,stored=pending
    journal.mark_attempt(item,time.time())
    if stored:
        if witness is not None:witness['branch']='stored-readback'
        envelope=json.loads(stored)
        envelope_witness(witness,envelope['request'])
        if verify(call,envelope,item,pending=True):
            journal.acknowledge(item,tx,envelope['version'])
            return envelope['version']
        # A negative readback alone is not absence: partial or conflicting
        # facts keep the exact effect frozen. Native completion must precede
        # a fresh absence proof; HTTP timeout never proves worker termination.
        if not completed_without_effect(call,journal,envelope,item,diagnostics=diagnostics):
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
    if completion:
        envelope['write_id']=secrets.token_hex(32)
    envelope_witness(witness,request)
    if previous and canonical.version==previous[0]:
        if witness is not None:witness['branch']='same-version'
        if not verify_fused(call,envelope,item,diagnostics=diagnostics):
            raise custody.CustodyUnknown('same native version lost projection; no automatic repair/replay')
        journal.prepare(item,tx,envelope)
    else:
        if witness is not None:witness['branch']='new-write'
        journal.prepare(item,tx,envelope)
        wrote()
        if witness is not None:witness['own_write']='attempted'
        try:
            response=call('/knot',request,**({'write_id':envelope['write_id']} if completion else {}))
        except (OSError,RuntimeError) as exc:
            if not completion or not response_lost(exc):
                raise
            if diagnostics is not None:diagnostics['completion']='response-lost'
            response=finished_after_response_loss(call,envelope)
            if diagnostics is not None:diagnostics['completion']='finished-after-response-loss'
        if (not isinstance(response,dict) or response.get('conforms') is False
            or not response.get('replaced') or type(response.get('tx_id')) is not int):
            raise custody.CustodyUnknown('projection write outcome unproved; preserve exact pending effect')
        if witness is not None:witness['own_write']='proved'
        if not verify(call,envelope,item):
            raise custody.CustodyUnknown('projection effect not readable; preserve exact pending effect')
    journal.acknowledge(item,tx,envelope['version'])
    return envelope['version']


def tick(reader,journal,post,*,changes,plane,actor,clock=time.monotonic,
         cursor_time=None,wall=time.time,completion=False):
    requests=0;writes=0;slowest=0.0;last=None
    tick_started=clock()
    def wrote():
        nonlocal writes
        writes+=1
    def call(endpoint,body,**kwargs):
        nonlocal requests
        requests+=1
        if requests>TICK_REQUESTS:raise custody.CustodyUnknown('tick request budget exhausted')
        response=timed(post,endpoint,body,**kwargs)
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
    # Set only by a page that proves the lane holds nothing newer for this
    # graph: an empty valid page, or one read through the watermark.
    caught_up=False
    try:
        pending=journal.next()
        feed_error=None
        backlog=journal.backlog_count()
        if backlog < custody.MAX_PENDING and changes is not None:
            page_limit=custody.MAX_TRANSACTIONS
            retained_pages=0
            # Up to MAX_FEED_PAGES reads, including retries (aegis-etv). The first page keeps the
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
                    page=timed(changes,since=journal.cursor,limit=page_limit,
                                 capture='new_values',graph=reader.board.graph)
                    journal.retain(page)
                    retained_pages+=1
                    feed_error=None
                    # The feed is graph-filtered while watermark_tx is global, so a
                    # quiet board answers an empty page with next_tx == since, far
                    # below the watermark. That empty page is the caught-up proof.
                    caught_up=(isinstance(page,dict) and (page.get('records')==[] or
                               integer_or_none(page.get('next_tx'))==integer_or_none(page.get('watermark_tx'))))
                except custody.CustodyFull:
                    # retain() rolled back the entire page, including its cursor.
                    # Spend another EXISTING feed-read slot on fewer whole txs;
                    # never split a transaction or expand custody/tick budgets.
                    if not retained_pages:feed_error='CustodyFull'
                    if page_limit>1:
                        page_limit=max(1,page_limit//2)
                        continue
                    if retained_pages:break
                    if pending is None:raise
                    feed_error='CustodyFull';break
                except (ValueError,OSError,RuntimeError,TypeError,KeyError,sqlite3.Error) as exc:
                    if not retained_pages and pending is None:raise
                    feed_error=type(exc).__name__;break
                backlog=journal.backlog_count()
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
            receipt.pop('fused_control',None)
            receipt.pop('completion_disposition',None)
            reset_witness()
            last=None
            # Diagnostic validation must not alter delivery or custody decisions.
            try:reader.board.subject(pending[0])
            except native.NativeUnknown:pass
            else:receipt['attempted_item']=pending[0]
            receipt['status']='UNKNOWN'
            version=deliver(reader,journal,call,pending,plane=plane,actor=actor,wrote=wrote,completion=completion,
                            witness=receipt['envelope_witness'] if 'attempted_item' in receipt else None,diagnostics=receipt)
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
                   backlog=journal.backlog_count(),retired_effects=journal.retired_count())
    return receipt
