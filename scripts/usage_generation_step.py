"""One bounded, pending-before-send cost step; no timer or implicit enrollment.

Only a reviewed caller holding the cost producer lane may invoke this primitive.
The wire body and verification remain domain-owned. An uncertain write is never
retried here. The shared journal counts C and A attempts, including failures.
"""
from contextlib import contextmanager, nullcontext
import fcntl
import hashlib
import json
import os
from pathlib import Path
import time

from usage_generation_budget import admit, MAX_REQUESTS


def atomic(path, value):
    path = Path(path)
    temporary = path.with_name(path.name+'.tmp')
    with temporary.open('w') as out:
        json.dump(value,out,sort_keys=True,separators=(',',':'))
        out.flush(); os.fsync(out.fileno())
    os.replace(temporary,path)
    fd=os.open(path.parent,os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)


@contextmanager
def locked(state):
    path=Path(state).with_suffix('.generations.lock')
    with path.open('a') as handle:
        try: fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: raise ValueError('cost producer lane busy') from None
        try: yield
        finally: fcntl.flock(handle,fcntl.LOCK_UN)


def run_step(kind, body, state_path, *, now, post, verify, preflight=None,
             records=0, staged_bytes=0, clock=time.monotonic, lane_locked=False):
    """One write, exact-body pending fence, bounded queries and conservative rate.

    post(endpoint, body) is the existing reviewed transport, never an alternate
    store or credential resolver. verify(call, accepted) must establish exact
    reader-route proof and may issue at most one query. Success clears pending
    only after the verified receipt is durably written. No cleanup on error.
    """
    state_path=Path(state_path)
    pending=state_path.with_suffix('.pending.json')
    ledger_path=state_path.with_suffix('.writes.json')
    receipt_path=state_path.with_suffix('.generation-receipt.json')
    with (nullcontext() if lane_locked else locked(state_path)):
        if pending.exists():
            raise ValueError('INDETERMINATE: reconcile pending write before any resend')
        previous=json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
        if not isinstance(previous,dict) or previous.get('next_request_after',0)>now:
            raise ValueError('BACKOFF: previous request budget cooldown')
        ledger=json.loads(ledger_path.read_text()) if ledger_path.exists() else []
        encoded=json.dumps(body,sort_keys=True).encode()
        maximum=MAX_REQUESTS.get(kind)
        if maximum is None or preflight is not None and kind!='part':
            raise ValueError('invalid cost action/preflight')
        proposed=admit(kind,len(encoded),records,maximum,now,ledger,staged_bytes)
        receipt={'at':now,'status':'UNKNOWN','requests':0,'write_sent':False,
                 'body_sha256':hashlib.sha256(encoded).hexdigest(),'bytes':len(encoded),
                 'kind':kind,'request_seconds':0,'knot_seconds':0}
        last=None
        def call(endpoint, payload):
            nonlocal last
            if endpoint!='/query' or receipt['requests']>=maximum:
                raise ValueError('only the bounded query slot is available')
            if last is not None: time.sleep(max(0,1-(clock()-last)))
            last=clock();receipt['requests']+=1;started=clock()
            try: return post(endpoint,payload)
            finally: receipt['request_seconds']+=clock()-started
        try:
            if preflight is not None and preflight(call) is not True:
                raise ValueError('canonical preflight unproven')
            if receipt['requests']>=maximum:
                raise ValueError('request budget exhausted before write')
            # Persist the body and attempt budget BEFORE networking. A crash can
            # conservatively consume a slot; it must never enable a resend.
            atomic(pending,{'endpoint':('/graph/create' if kind=='graph' else '/graph/label' if kind=='graph_label' else '/knot'),
                            'body':body,'receipt':dict(receipt)})
            atomic(ledger_path,proposed)
            if last is not None: time.sleep(max(0,1-(clock()-last)))
            last=clock();receipt['requests']+=1;receipt['write_sent']=True;started=clock()
            try: accepted=post('/graph/create' if kind=='graph' else '/graph/label' if kind=='graph_label' else '/knot',body)
            finally:
                elapsed=clock()-started;receipt['knot_seconds']=elapsed
                receipt['request_seconds']+=elapsed
            if not isinstance(accepted,dict) or accepted.get('conforms') is False:
                raise ValueError('write refused/unknown')
            if kind=='graph':
                if type(accepted.get('g')) is not int:
                    raise ValueError('graph registration receipt missing')
            elif type(accepted.get('tx_id')) is not int:
                raise ValueError('write transaction receipt missing')
            verification_start=receipt['requests']
            if verify(call,accepted) is not True or receipt['requests']-verification_start!=1:
                raise ValueError('one exact graph readback unproven')
            receipt.update(status='VERIFIED',accepted=accepted)
            slow=receipt['knot_seconds']>10 or receipt['request_seconds']>15
            receipt['next_request_after']=now+(900 if slow else 60)
            atomic(receipt_path,receipt)
            pending.unlink()
            return receipt
        finally:
            slow=receipt['knot_seconds']>10 or receipt['request_seconds']>15
            receipt['next_request_after']=now+(900 if slow else 60)
            if pending.exists(): receipt['status']='STALLED'
            atomic(receipt_path,receipt)
