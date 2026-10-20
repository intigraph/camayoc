#!/usr/bin/env python3
# arming: agent-invoked default OFF; discovery/read-cost and schedule need review.
"""Bounded Seeds discovery into the existing serial native metadata writer.

This is a current-state sweep, NOT an as-of snapshot or change-feed cursor.
New IDs sorting behind the checkpoint require a separately admitted new sweep.
Never open the standing ingress journal here. No br or local tracker fallback.
"""
from __future__ import annotations
import argparse
import fcntl
import json
from pathlib import Path
import sqlite3
import sys
import time
import planes
import seed_change_custody as custody
import seed_work_items as native
import seed_work_item_sync as delivery

PAGE = 16
FORMAT = 'camayoc-seeds-backfill-v1'


def discover(post, board, after):
    query = (f'SELECT ?id WHERE {{ GRAPH <{board.graph}> {{ '
             f'?s a <{native.SCHEMA}Action> ; <{native.SCHEMA}identifier> ?id . '
             f'FILTER(STR(?id) > {json.dumps(after)}) }} }} ORDER BY ?id LIMIT {PAGE+1}')
    body = post('/query', {'query': query})
    if (not isinstance(body, dict) or body.get('truncated') is not False
            or not isinstance(body.get('rows'), list)
            or len(body['rows']) > PAGE+1
            or len(json.dumps(body).encode()) > native.MAX_BYTES):
        raise native.NativeUnknown('discovery partial or outside budget')
    ids = []
    for row in body['rows']:
        if not isinstance(row, dict) or set(row) != {'id'}:
            raise native.NativeUnknown('discovery identity missing')
        item = native.lexical(row['id'])
        board.subject(item)
        if item <= (ids[-1] if ids else after):
            raise native.NativeUnknown('discovery duplicate, unordered or regressed')
        ids.append(item)
    return ids[:PAGE], len(ids) <= PAGE


class Sweep(custody.Journal):
    def __init__(self, path, board, *, server, plane, actor, initialise=False):
        # Refuse another adapter's state BEFORE opening it read/write. This never
        # migrates/adopts the standing native ingress or a legacy JSON journal.
        exists = path.exists()
        if exists:
            db = sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)
            try:
                row = db.execute('SELECT format,plane,actor FROM backfill').fetchall()
                if row != [(FORMAT, plane, actor)]:
                    raise custody.CustodyUnknown('foreign backfill identity')
            finally:
                db.close()
            if initialise:
                raise custody.CustodyUnknown('existing sweep cannot be initialised/reset')
        elif not initialise:
            raise custody.CustodyUnknown('new sweep needs explicit reviewed initialisation')
        super().__init__(path, board, server=server, initial_tx=None if exists else 0)
        if not exists:
            self.db.execute('CREATE TABLE backfill(format TEXT,plane TEXT,actor TEXT,after_id TEXT,exhausted INTEGER)')
            self.db.execute('INSERT INTO backfill VALUES(?,?,?,?,?)', (FORMAT, plane, actor, '', 0))

    def discover(self, post):
        after, exhausted = self.db.execute('SELECT after_id,exhausted FROM backfill').fetchone()
        if exhausted or self.next() is not None:
            return
        ids, final = discover(post, self.board, after)
        if not ids and not after:
            raise native.NativeUnknown('empty initial board is not positive source coverage')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            for item in ids:
                # tx=0 is a LOCAL queue token, never a claimed source transaction.
                self.db.execute('INSERT INTO pending(id,tx) VALUES(?,0)', (item,))
            self.db.execute('UPDATE backfill SET after_id=?,exhausted=?', (ids[-1] if ids else after, int(final)))
            self.db.execute('COMMIT')
        except Exception:
            self.db.execute('ROLLBACK')
            raise


def tick(reader, journal, post, *, plane, actor, clock=time.monotonic):
    calls = writes = verified = 0
    slowest = 0.0
    def call(endpoint, body):
        nonlocal calls, slowest
        calls += 1
        if calls > delivery.TICK_REQUESTS:
            raise custody.CustodyUnknown('backfill request budget exceeded')
        started = clock()
        try:
            return post(endpoint, body)
        finally:
            slowest = max(slowest, clock()-started)
    def wrote():
        nonlocal writes
        writes += 1
    result = {'status': 'UNKNOWN'}
    try:
        journal.discover(call)
        seen = set()
        while (pending := journal.next()) is not None:
            if (len(seen) >= delivery.MAX_DELIVERIES or pending[0] in seen
                    or calls+delivery.ITEM_REQUESTS > delivery.TICK_REQUESTS
                    or (seen and slowest > delivery.READ_BUDGET_SECONDS)):
                break
            seen.add(pending[0])
            delivery.deliver(reader, journal, call, pending, plane=plane, actor=actor, wrote=wrote)
            verified += 1
        exhausted = journal.db.execute('SELECT exhausted FROM backfill').fetchone()[0]
        result.update(status='OK', enumeration_exhausted=bool(exhausted))
    except Exception as error:
        # Never print transport errors, credentials, records, or pending bodies.
        result['error'] = type(error).__name__
    return {**result, 'requests': calls, 'writes': writes, 'verified': verified,
            'backlog': journal.db.execute('SELECT COUNT(*) FROM pending').fetchone()[0]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--enable', action='store_true')
    parser.add_argument('--native-authority-stdin', action='store_true')
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--actor', required=True)
    parser.add_argument('--initialise', action='store_true')
    args = parser.parse_args(argv)
    if not args.enable:
        print('{"status":"OFF","requests":0,"writes":0}')
        return 0
    journal = None
    try:
        if not args.native_authority_stdin or args.state.suffix != '.sqlite3':
            raise native.NativeUnknown('protected authority and dedicated sqlite3 custody required')
        raw = sys.stdin.buffer.read(4097)
        if len(raw) > 4096:
            raise native.NativeUnknown('authority exceeds budget')
        post = lambda route, body: planes._post(route, body, client='camayoc-ingress', response_limit=native.MAX_BYTES)
        reader = native.from_authority(post, json.loads(raw), server=planes.SERVER)
        plane = planes.plane_for('observed')
        # Parent must already exist; this command never installs a schedule/store.
        with args.state.with_suffix('.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            journal = Sweep(args.state, reader.board, server=planes.SERVER, plane=plane,
                            actor=args.actor, initialise=args.initialise)
            receipt = tick(reader, journal, post, plane=plane, actor=args.actor)
    except Exception as error:
        receipt = {'status': 'UNKNOWN', 'error': type(error).__name__}
    finally:
        if journal is not None:
            journal.db.close()
    print(json.dumps(receipt, sort_keys=True))
    return 0 if receipt['status'] == 'OK' else 2


if __name__ == '__main__':
    sys.exit(main())
