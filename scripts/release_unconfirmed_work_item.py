"""Release unconfirmed WorkItem pending effects so normal delivery re-projects them.

An effect whose writer call was never confirmed (for example an HTTP 502, or a
v1 completion receipt lost to a server restart) stays frozen by design:
delivery only reads it back and never resends. When the operator holds
terminal-worker evidence (for example the server process started after the
effect's last write), this command proves non-application again right before
acting and returns each row to a fresh candidate. The next normal tick reads
the native item, prepares a new exact effect and writes once.

Non-application is proved per item by exact readback: either no triple about
the subject in any graph, or exactly one complete canonical projection of an
older native version of the same item in the target graph (the verified one
when custody holds it). Nothing else is accepted.

At most MAX_ITEMS exact items per run, each proved and released on its own.
This command never writes to the server, never resends, never touches any
other row, and takes the producer's own state lock so it cannot race a tick.
Exit 0 all released, 3 any refused (refused rows unchanged), 2 any unknown.
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

#: One bounded operator batch. A whole frozen burst fits; a backlog never does.
MAX_ITEMS = 64


def release(journal, post, item):
    """Return a payload-free receipt. Changes at most this one row."""
    row = journal.db.execute('SELECT body FROM pending WHERE id=?', (item,)).fetchone()
    if row is None or row[0] is None:
        return {'status': 'REFUSED', 'item': item, 'reason': 'no stored pending effect'}
    try:
        envelope = json.loads(row[0])
    except ValueError:
        return {'status': 'REFUSED', 'item': item, 'reason': 'stored effect unreadable'}
    if not isinstance(envelope, dict):
        return {'status': 'REFUSED', 'item': item, 'reason': 'stored effect unreadable'}
    previous = journal.db.execute('SELECT version FROM verified WHERE id=?', (item,)).fetchone()
    if previous and (type(envelope.get('version')) is not int or envelope['version'] <= previous[0]):
        return {'status': 'REFUSED', 'item': item, 'reason': 'already verified'}
    # A failed read is not a refusal: it propagates, and release_many reports
    # this item UNKNOWN (exit 2) rather than REFUSED (exit 3). Nothing changed.
    proof = sync.effect_not_applied(post, journal, envelope, item)
    if proof is None:
        return {'status': 'REFUSED', 'item': item, 'reason': 'non-application not proved'}
    tx = journal.release_unconfirmed(item, row[0])
    return {'status': 'RELEASED', 'item': item, 'tx': tx, 'proof': proof}


def release_many(journal, post, items):
    """Prove and release each exact item independently; one refusal stops none."""
    receipts = []
    for item in items:
        try:
            receipts.append(release(journal, post, item))
        except Exception as error:
            # Never print transport errors, credentials, records or pending bodies.
            receipts.append({'status': 'UNKNOWN', 'item': item, 'error': type(error).__name__})
    return receipts


def aggregate(receipts):
    states = {r['status'] for r in receipts}
    return 'UNKNOWN' if 'UNKNOWN' in states else 'REFUSED' if 'REFUSED' in states else 'RELEASED'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--enable', action='store_true')
    parser.add_argument('--native-authority-stdin', action='store_true')
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--item', required=True, action='append',
                        help=f'exact native ID; repeat for up to {MAX_ITEMS}')
    args = parser.parse_args(argv)
    if not args.enable:
        print('{"status":"OFF"}')
        return 0
    items = args.item
    journal = None
    receipts = None
    try:
        if len(items) > MAX_ITEMS or len(set(items)) != len(items):
            raise custody.CustodyUnknown('item list exceeds the batch bound or repeats an item')
        if (not args.native_authority_stdin or args.state.suffix != '.sqlite3'
                or not args.state.is_file()):
            raise custody.CustodyUnknown('existing dedicated sqlite3 custody and protected authority required')
        raw = sys.stdin.buffer.read(4097)
        if len(raw) > 4096:
            raise native.NativeUnknown('authority exceeds budget')
        post = lambda route, body: planes._post(route, body, client='camayoc-ingress',
                                                response_limit=native.MAX_BYTES)
        reader = native.from_authority(post, json.loads(raw), server=planes.SERVER)
        for item in items:
            reader.board.subject(item)  # exact native ID for this board, or refuse all
        with args.state.with_suffix('.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            journal = custody.Journal(args.state, reader.board, server=planes.SERVER)
            receipts = release_many(journal, post, items)
    except Exception as error:
        # Never print transport errors, credentials, records or pending bodies.
        receipts = [{'status': 'UNKNOWN', 'item': item, 'error': type(error).__name__}
                    for item in items]
    finally:
        if journal is not None:
            journal.db.close()
    status = aggregate(receipts)
    if len(items) == 1:
        print(json.dumps(receipts[0], sort_keys=True))
    else:
        print(json.dumps({'status': status, 'items': receipts}, sort_keys=True))
    return {'RELEASED': 0, 'REFUSED': 3}.get(status, 2)


if __name__ == '__main__':
    sys.exit(main())
