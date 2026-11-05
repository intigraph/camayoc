"""Release ONE unconfirmed WorkItem pending effect so normal delivery re-projects it.

An effect whose writer call was never confirmed (for example an HTTP 502) stays
frozen by design: delivery only reads it back and never resends. When the
operator has evidence that nothing landed, this command proves it again right
before acting and returns that one row to a fresh candidate. The next normal
tick then reads the native item, prepares a new exact effect and writes once.

This command never writes to the server, never resends, never touches any other
row, and takes the producer's own state lock so it cannot race a tick.
Exit 0 released, 3 refused (nothing changed), 2 unknown.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import sys
from pathlib import Path

import planes
import seed_change_custody as custody
import seed_work_item_sync as sync
import seed_work_items as native


def release(journal, post, item):
    """Return a payload-free receipt. Changes at most this one row."""
    row = journal.db.execute('SELECT body FROM pending WHERE id=?', (item,)).fetchone()
    if row is None or row[0] is None:
        return {'status': 'REFUSED', 'item': item, 'reason': 'no stored pending effect'}
    if journal.db.execute('SELECT 1 FROM verified WHERE id=?', (item,)).fetchone():
        return {'status': 'REFUSED', 'item': item, 'reason': 'already verified'}
    if not sync.prove_absent(post, item):
        return {'status': 'REFUSED', 'item': item, 'reason': 'server absence not proved'}
    tx = journal.release_unconfirmed(item, row[0])
    return {'status': 'RELEASED', 'item': item, 'tx': tx}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--enable', action='store_true')
    parser.add_argument('--native-authority-stdin', action='store_true')
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--item', required=True, help='exact native ID; one per run')
    args = parser.parse_args(argv)
    if not args.enable:
        print('{"status":"OFF"}')
        return 0
    journal = None
    try:
        if (not args.native_authority_stdin or args.state.suffix != '.sqlite3'
                or not args.state.is_file()):
            raise custody.CustodyUnknown('existing dedicated sqlite3 custody and protected authority required')
        raw = sys.stdin.buffer.read(4097)
        if len(raw) > 4096:
            raise native.NativeUnknown('authority exceeds budget')
        post = lambda route, body: planes._post(route, body, client='camayoc-ingress',
                                                response_limit=native.MAX_BYTES)
        reader = native.from_authority(post, json.loads(raw), server=planes.SERVER)
        reader.board.subject(args.item)  # exact native ID for this board, or refuse
        with args.state.with_suffix('.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            journal = custody.Journal(args.state, reader.board, server=planes.SERVER)
            receipt = release(journal, post, args.item)
    except Exception as error:
        # Never print transport errors, credentials, records or pending bodies.
        receipt = {'status': 'UNKNOWN', 'item': args.item, 'error': type(error).__name__}
    finally:
        if journal is not None:
            journal.db.close()
    print(json.dumps(receipt, sort_keys=True))
    return {'RELEASED': 0, 'REFUSED': 3}.get(receipt['status'], 2)


if __name__ == '__main__':
    sys.exit(main())
