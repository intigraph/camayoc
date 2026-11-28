#!/usr/bin/env python3
"""Explicit bounded-effect scheduling retirement, not receiver completion or replay.

Use the producer's protected authority and lock. Inspect returns only bounded
bindings; full frozen envelopes remain queryable in retired_effect.body. Release
undoes exclusion only: the pending body must still pass native reconciliation.
Retire performs two bounded absence reads; inspect/release perform no HTTP calls.
No journal creation, cursor reset or automatic retirement.

retire --superseded (aegis-xdvg.5) is the bounded exception for a PRESENT
subject: the target graph must hold exactly one complete canonical projection of
an OLDER version (effect_not_applied == 'prior-version'), and the receiving
server's process must have started AFTER the attempt ended. Completion receipts
are process-local, so a worker in an exited process cannot commit. The start
time is read twice and must not change. The send bound is the journal's
recorded attempt end, or for effects older than that record, an operator bound
(--sent-before) with --ruling. Same scheduling exclusion, envelope retained,
no resend.
"""
from __future__ import annotations
import argparse
import fcntl
import json
import sqlite3
from pathlib import Path
import sys
import time
import planes
import re
import seed_change_custody as custody
import seed_work_items as native
import seed_work_item_sync as sync


def operate(journal,args,post=None):
    after=getattr(args,'after_slot',0);limit=getattr(args,'history_limit',32)
    if type(after) is not int or after<0 or type(limit) is not int or not 1<=limit<=64:
        raise custody.CustodyUnknown('history page bound invalid')
    if args.action=='retire':
        def absent(item,envelope):
            if post is None:return False
            def bounded_query(endpoint,body):
                response=post(endpoint,body)
                # Quipu's canonical ASK response has no row pagination and
                # omits SELECT's truncation field. Admit only its exact known
                # shape; a missing flag on any other response stays UNKNOWN.
                canonical_ask=(isinstance(response,dict)
                               and set(response)=={'labels','result'}
                               and response['labels'] is None
                               and type(response['result']) is bool)
                if (not isinstance(response,dict)
                        or not (response.get('truncated') is False or canonical_ask)
                        or type(response.get('result')) is not bool
                        or len(json.dumps(response).encode())>4096):
                    raise custody.CustodyUnknown('bounded absence response unproved')
                return response
            return sync.prove_absent(bounded_query,item)
        now=time.time()
        if getattr(args,'superseded',False):
            proof,reason=superseded_proof(journal,args,post,now)
            journal.retire_effect(args.item,write_id=args.write_id,request_sha256=args.request_sha256,
                                 version=args.version,effect_tx=args.effect_tx,reason=reason,now=now,
                                 prove_superseded=proof)
        else:
            journal.retire_effect(args.item,write_id=args.write_id,request_sha256=args.request_sha256,
                                 version=args.version,effect_tx=args.effect_tx,reason=args.reason,now=now,
                                 prove_absent=absent)
    elif args.action=='release':
        journal.release_retired(args.item,write_id=args.write_id,
                                request_sha256=args.request_sha256,reason=args.reason)
    # No payload, graph, description or token in stdout. The SQLite ledger
    # preserves the complete envelope and exclusion history under its own ACL.
    fields=('slot','id','tx','effect_tx','write_id','request_sha256','version','reason','retired_at','active','release_reason')
    rows=journal.db.execute('SELECT '+','.join(fields)+' FROM retired_effect WHERE slot>? ORDER BY slot LIMIT ?',
                            (after,limit+1)).fetchall()
    more=len(rows)>limit;rows=rows[:limit]
    active=journal.db.execute('SELECT '+','.join(fields)+' FROM retired_effect WHERE active=1 ORDER BY slot LIMIT 9').fetchall()
    if len(active)>custody.MAX_RETIRED:raise custody.CustodyUnknown('active retirement inspection exceeds bound')
    return {'status':'OK','action':args.action,'retired_effects':journal.retired_count(),
            'active_records':[dict(zip(fields,row)) for row in active],
            'records':[dict(zip(fields,row)) for row in rows],
            'history_has_more':more,'history_next_after':rows[-1][0] if more else None}


METRICS_LIMIT=sync.METRICS_LIMIT
#: One parser for the receiving process start, shared with the in-tick
#: restart fence (aegis-moy).
process_start=sync.receiving_process_start


def server_get(path):
    return sync.server_get(planes.SERVER)(path)


def superseded_proof(journal,args,post,now,get=server_get):
    ruling=getattr(args,'ruling',None)
    if not isinstance(ruling,str) or not re.fullmatch('[a-z]+-[a-z0-9.]+',ruling):
        raise custody.CustodyUnknown('superseded retirement needs a ruling item')
    if not isinstance(args.reason,str) or not args.reason.strip():
        raise custody.CustodyUnknown('exact retirement binding or operator reason unproved')
    recorded=journal.send_ended(args.item,args.write_id)
    operator=getattr(args,'sent_before',None)
    if recorded is None and operator is None:
        raise custody.CustodyUnknown('no send bound: no recorded attempt end and no --sent-before')
    if operator is not None and (type(operator) not in (int,float) or not 0<operator<=now
                                 or (recorded is not None and operator<recorded)):
        raise custody.CustodyUnknown('operator send bound contradicts the journal')
    bound=recorded if recorded is not None else operator
    first=process_start(get)
    if not bound<first<=now:
        raise custody.CustodyUnknown('receiving process did not start after the attempt ended')
    def proof(item,envelope):
        if post is None:return False
        def bounded(endpoint,body):
            response=post(endpoint,body)
            if not isinstance(response,dict) or len(json.dumps(response).encode())>65536:
                raise custody.CustodyUnknown('bounded superseded read unproved')
            return response
        if sync.effect_not_applied(bounded,journal,envelope,item)!='prior-version':return False
        # Read the start again after the graph read: a restart in between is not proof.
        return process_start(get)==first
    reason=(f"{ruling} superseded: older complete version present; receiving process start "
            f"{first:.0f} > send bound {bound:.0f} ({'journal' if recorded is not None else 'operator'}); "
            f"{args.reason.strip()}")[:512]
    return proof,reason


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=('inspect','retire','release'))
    p.add_argument('--after-slot',type=int,default=0,help='history keyset cursor; default first page')
    p.add_argument('--history-limit',type=int,default=32,help='history page size, 1..64; active bindings always included')
    p.add_argument('--enable',action='store_true')
    p.add_argument('--native-authority-stdin',action='store_true')
    p.add_argument('--state',type=Path,required=True)
    p.add_argument('--item');p.add_argument('--write-id');p.add_argument('--request-sha256')
    p.add_argument('--version',type=int);p.add_argument('--effect-tx',type=int);p.add_argument('--reason')
    p.add_argument('--superseded',action='store_true',help='retire a PRESENT older-version subject after a receiving-process restart')
    p.add_argument('--ruling',help='ruling item that admits this one superseded retirement')
    p.add_argument('--sent-before',type=float,help='operator upper bound (unix seconds) on the send, for effects with no recorded attempt end')
    args=p.parse_args(argv)
    if (args.superseded or args.ruling or args.sent_before is not None) and args.action!='retire':
        p.error('--superseded, --ruling and --sent-before apply to retire only')
    if (args.ruling or args.sent_before is not None) and not args.superseded:
        p.error('--ruling and --sent-before need --superseded')
    if not args.enable:
        print('{"status":"OFF"}');return 0
    journal=None
    try:
        if not args.native_authority_stdin or args.state.suffix!='.sqlite3' or not args.state.is_file():
            raise custody.CustodyUnknown('existing dedicated custody and protected authority required')
        raw=sys.stdin.buffer.read(4097)
        if len(raw)>4096:raise custody.CustodyUnknown('authority exceeds budget')
        reader=native.from_authority(None,json.loads(raw),server=planes.SERVER)
        if args.action!='inspect':reader.board.subject(args.item)
        with args.state.with_suffix('.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            probe=sqlite3.connect(args.state.resolve().as_uri()+'?mode=ro',uri=True)
            try:
                if probe.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='backfill'").fetchone():
                    raise custody.CustodyUnknown('backfill custody is not standing ingress')
            finally:probe.close()
            journal=custody.Journal(args.state,reader.board,server=planes.SERVER)
            limit=65536 if args.superseded else 4096
            result=operate(journal,args,post=lambda endpoint,body: planes._post(endpoint,body,client='camayoc-ingress',response_limit=limit))
    except (custody.CustodyUnknown,native.NativeUnknown) as exc:
        result={'status':'REFUSED','error':type(exc).__name__}
    except Exception as exc:
        result={'status':'UNKNOWN','error':type(exc).__name__}
    finally:
        if journal is not None:journal.db.close()
    print(json.dumps(result,sort_keys=True))
    return {'OK':0,'REFUSED':3,'OFF':0}.get(result['status'],2)


if __name__=='__main__':sys.exit(main())
