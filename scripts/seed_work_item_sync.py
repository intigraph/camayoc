"""One bounded native metadata projection per tick; effects never blindly resent.

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
    # Separate single-pattern control, avoiding the asserted-type/label join.
    control=post('/query',{'query':f'SELECT ?t WHERE {{ GRAPH <{graph}> {{ <{subject}> a ?t }} }}'})
    if (not isinstance(control,dict) or control.get('truncated') or
        not isinstance(control.get('rows'),list) or
        not any(row.get('t') in (projection.A+'WorkItem','aegis:WorkItem') for row in control['rows'])):
        raise custody.CustodyUnknown('metadata WorkItem control unproved')
    found=post('/query',{'query':f'SELECT ?v WHERE {{ GRAPH <{graph}> {{ <{subject}> <{native.RDFS}comment> ?v }} }} LIMIT 129'})
    rows=found.get('rows') if isinstance(found,dict) else None
    if not isinstance(rows,list) or found.get('truncated') or len(rows)>128:
        raise custody.CustodyUnknown('metadata readback partial')
    expected=envelope['description']
    # One exact attributable current description; unrelated comments need not
    # disappear. They are not authority and are never consumed as native status.
    return sum(native.lexical(row.get('v'))==expected for row in rows)==1


def tick(reader,journal,post,*,changes,plane,actor):
    requests=0;writes=0
    def call(endpoint,body):
        nonlocal requests
        requests+=1
        if requests>5:raise custody.CustodyUnknown('tick request budget exhausted')
        return post(endpoint,body)
    receipt={'status':'UNKNOWN','writes':0,'requests':0,'verified':0}
    try:
        pending=journal.next()
        feed_error=None
        backlog=journal.db.execute('SELECT COUNT(*) FROM pending').fetchone()[0]
        if backlog < custody.MAX_PENDING:
            requests+=1
            try:
                page=changes(since=journal.cursor,limit=custody.MAX_TRANSACTIONS,
                             capture='new_values',graph=reader.board.graph)
                journal.retain(page)
            except (ValueError,OSError,RuntimeError,TypeError,KeyError,sqlite3.Error) as exc:
                if pending is None:raise
                feed_error=type(exc).__name__
            pending=journal.next()
        else:
            feed_error='PendingCapacityReached'
        if pending is None:
            receipt['status']='OK'
        else:
            item,tx,stored=pending
            journal.mark_attempt(item,time.time())
            if stored:
                envelope=json.loads(stored)
                if not verify(call,envelope,item):
                    raise custody.CustodyUnknown('pending effect indeterminate; freeze, no resend')
            else:
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
                    writes=1
                    response=call('/knot',request)
                    if (not isinstance(response,dict) or response.get('conforms') is False
                        or not response.get('replaced') or type(response.get('tx_id')) is not int):
                        raise custody.CustodyUnknown('projection write outcome unproved; preserve exact pending effect')
                    if not verify(call,envelope,item):
                        raise custody.CustodyUnknown('projection effect not readable; preserve exact pending effect')
            journal.acknowledge(item,tx,envelope['version'])
            receipt.update(status='OK',verified=1,item=item,version=envelope['version'])
        if feed_error:
            receipt.update(status='UNKNOWN',feed_error=feed_error)
    except (ValueError,OSError,RuntimeError,TypeError,KeyError,sqlite3.Error) as exc:
        receipt['error']=type(exc).__name__
    receipt.update(requests=requests,writes=writes,cursor=journal.cursor,
                   backlog=journal.db.execute('SELECT COUNT(*) FROM pending').fetchone()[0])
    return receipt
