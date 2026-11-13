#!/usr/bin/env python3
"""Explicit bounded-effect scheduling retirement, not receiver completion or replay.

Use the producer's protected authority and lock. Inspect returns only bounded
bindings; full frozen envelopes remain queryable in retired_effect.body. Release
undoes exclusion only: the pending body must still pass native reconciliation.
Retire performs two bounded absence reads; inspect/release perform no HTTP calls.
No journal creation, cursor reset or automatic retirement.
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
                if (not isinstance(response,dict) or response.get('truncated') is not False
                        or type(response.get('result')) is not bool
                        or len(json.dumps(response).encode())>4096):
                    raise custody.CustodyUnknown('bounded absence response unproved')
                return response
            return sync.prove_absent(bounded_query,item)
        journal.retire_effect(args.item,write_id=args.write_id,request_sha256=args.request_sha256,
                             version=args.version,effect_tx=args.effect_tx,reason=args.reason,now=time.time(),
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
    args=p.parse_args(argv)
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
            result=operate(journal,args,post=lambda endpoint,body: planes._post(endpoint,body,client='camayoc-ingress',response_limit=4096))
    except (custody.CustodyUnknown,native.NativeUnknown) as exc:
        result={'status':'REFUSED','error':type(exc).__name__}
    except Exception as exc:
        result={'status':'UNKNOWN','error':type(exc).__name__}
    finally:
        if journal is not None:journal.db.close()
    print(json.dumps(result,sort_keys=True))
    return {'OK':0,'REFUSED':3,'OFF':0}.get(result['status'],2)


if __name__=='__main__':sys.exit(main())
