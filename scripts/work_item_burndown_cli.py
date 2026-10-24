#!/usr/bin/env python3
"""Explicit event ingress/scrape; no automatic scope, enrollment, or schedule."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import fcntl
import json
import sqlite3
from pathlib import Path
import sys

import planes
import seed_work_items as n
import work_item_burndown as b
import work_item_burndown_io as io
import work_item_burndown_metrics as metrics
import windows


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['enroll', 'ingress', 'scrape', 'definition'])
    p.add_argument('--state', type=Path, required=True)
    p.add_argument('--owner', required=True)
    p.add_argument('--priority', type=int, required=True)
    p.add_argument('--month', required=True, help='reviewed enrollment window; never auto-rolls')
    p.add_argument('--initial-tx', type=int)
    p.add_argument('--native-authority-stdin', action='store_true', required=True)
    args = p.parse_args()
    now = datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')
    try:
        authority = json.load(sys.stdin)
        server = authority.get('server')
        reader = n.from_authority(None, authority, server=server)
        scope = b.Scope(reader.board, server, args.owner, args.priority, args.month)
        # A normal deployment-selected transport, not an ambient server override.
        planes.SERVER = server.rstrip('/')
        post = io.Transport(server, client=io.CLIENT+('-scrape' if args.command == 'scrape' else '-ingress'))
        args.state.parent.mkdir(parents=True, exist_ok=True)
        lock = args.state.with_suffix('.lock')
        if lock.is_symlink():
            raise b.Unknown('linked run lock')
        with lock.open('a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if args.command == 'definition':
                if args.initial_tx is not None:
                    raise b.Unknown('cursor only belongs to explicit enrollment')
                print(metrics.DEFINITION.read_text(), end='')
                return 0
            if args.command != 'enroll' and not args.state.exists():
                raise b.Unknown('custody absent; no automatic enrollment')
            if args.command != 'enroll' and args.initial_tx is not None:
                raise b.Unknown('existing cursor cannot be reset')
            journal = b.Journal(args.state, scope)
            try:
                if args.command == 'enroll':
                    if args.initial_tx is None or journal.db.execute('SELECT cursor FROM authority').fetchone()[0] is not None:
                        raise b.Unknown('one explicit reviewed committed source tx required for new enrollment')
                    # Exactly the normal DORA operational-window registration.
                    result = windows.ensure_window('workitem-burndown/'+scope.key, scope.month, now)
                    if result.get('iri') != scope.graph or type(result.get('label_tx')) is not int:
                        raise b.Unknown('operational graph registration/label unproved')
                    result = io.enroll(post, journal, cursor=args.initial_tx, observed_at=now)
                    print(json.dumps(result | {'requests': post.calls}, sort_keys=True))
                elif args.command == 'ingress':
                    result = io.tick(post, journal, observed_at=now)
                    print(json.dumps(result | {'requests': post.calls}, sort_keys=True))
                else:
                    if journal.pending():
                        raise b.Unknown('ingress effect pending; scrape is not accepted')
                    samples = metrics.collect(post, scope, now)
                    for name, labels, value in samples:
                        dims = ','.join(key+'='+json.dumps(text) for key, text in sorted(labels.items()))
                        print(name+('{'+dims+'}' if dims else '')+' '+str(value))
                    print('seeds_workitem_events_scrape_success 1')
                    print('seeds_workitem_events_last_success_timestamp_seconds '+str(datetime.now(timezone.utc).timestamp()))
            finally:
                journal.db.close()
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, sqlite3.Error) as error:
        # Never print credentials, payloads or raw transport exception messages.
        print('WorkItem burn-down UNKNOWN: '+type(error).__name__, file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
