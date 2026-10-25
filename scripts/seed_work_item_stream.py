"""Persistent transport for native metadata ingress, using existing durable custody.

No new enrollment, cursor bootstrap, scheduler or production activation here.
"""
from __future__ import annotations

import fcntl
import json
import math
import sys
import time

import planes
import seed_change_custody as custody
import seed_change_stream
import seed_work_item_sync as sync
import seed_work_items as native
import sync_work_items as driver


def run(args):
    if getattr(args, 'initial_tx', None) is not None:
        raise custody.CustodyUnknown('stream cannot initialize or reset custody')
    if not args.state.is_file() or args.state.suffix != '.sqlite3':
        raise custody.CustodyUnknown('stream needs existing native custody; no bootstrap')
    raw = sys.stdin.buffer.read(4097)
    if len(raw) > 4096:
        raise native.NativeUnknown('authority projection exceeds budget')
    reader = native.from_authority(lambda *args: None, json.loads(raw), server=planes.SERVER)
    with args.stream_identity.open('rb') as file:
        raw = file.read(4097)
    if len(raw) > 4096:
        raise custody.CustodyUnknown('stream identity exceeds budget')
    identity = json.loads(raw)
    with args.state.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        journal = custody.Journal(args.state, reader.board, server=planes.SERVER)
        stream = None
        try:
            last_success, previous_attempt = None, 0
            receipt_path = args.state.with_suffix('.receipt.json')
            if receipt_path.exists():
                previous = json.loads(receipt_path.read_text())
                previous_attempt = previous['attempted_at']
                last_success = previous.get('last_success_at')
                for value in (previous_attempt, last_success):
                    if value is not None and (type(value) not in (int, float)
                                             or not math.isfinite(value) or value < 0):
                        raise custody.CustodyUnknown('prior receipt clock unproved')
            headers = {'X-Quipu-Client': 'camayoc-ingress',
                       **planes.provenance_headers('camayoc-ingress')}
            token = planes.auth_token()
            if token:
                headers['Authorization'] = 'Bearer ' + token
            stream = seed_change_stream.Subscription(planes.SERVER, reader.board.graph,
                                                    journal.cursor, identity, headers)
            def post(endpoint, body):
                return planes._post(endpoint, body, client='camayoc-ingress')
            while True:
                # Commit candidates without waiting for the next effect tick.
                # Backpressure remains one complete transaction and MAX_PENDING.
                stream.retain_one(journal)
                now = time.time()
                if now < previous_attempt + driver.INTERVAL:
                    continue
                if stream.connected and stream.source_verified:
                    receipt = sync.tick(reader, journal, post, changes=None,
                                        plane=planes.plane_for('observed'), actor=args.actor)
                else:
                    receipt = {'status': 'UNKNOWN', 'writes': 0, 'requests': 0, 'verified': 0,
                               'cursor': journal.cursor,
                               'backlog': journal.db.execute('SELECT COUNT(*) FROM pending').fetchone()[0]}
                receipt.update(stream_connected=int(stream.connected), stream_errors=stream.errors,
                               stream_source_verified=int(stream.source_verified))
                if not stream.connected or not stream.source_verified:
                    receipt.update(status='UNKNOWN', feed_error='StreamUnverified')
                if receipt['status'] == 'OK':
                    last_success = now
                driver.save(receipt_path, {'attempted_at': now, 'last_success_at': last_success, **receipt})
                code = driver.publish_native_receipt(args, receipt, now, last_success, reader.board.graph)
                if code and receipt['status'] == 'OK':
                    return code  # A failed status publication is not success.
                previous_attempt = now  # Preserve the existing per-minute effect budget.
        finally:
            if stream is not None:
                stream.close()
            journal.db.close()
