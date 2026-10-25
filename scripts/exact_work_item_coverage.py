#!/usr/bin/env python3
"""Opt-in, read-only two-ID coverage receipt; never installs or arms a schedule.

Uses the legacy covered() semantics through an exact-subject bounded reader.
This receipt is deliberately separate from the global coverage cache.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import signal
import sys
import time
import urllib.request

import backfill_work_items as legacy
import planes
import quipu_auth
import seed_work_items as native

MAX_REQUESTS = 15
MAX_BYTES = 256 * 1024
DEADLINE = 70.0


class Unknown(ValueError):
    """Coverage was not established; never interpret this as an empty result."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Unknown('redirect refused')


class Transport:
    def __init__(self, server, credential, clock=time.monotonic):
        self.server = server.rstrip('/')
        parsed = urllib.parse.urlsplit(self.server)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname or
                parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path):
            raise Unknown('invalid server origin')
        self.credential = credential
        self.clock = clock
        self.end = clock() + DEADLINE
        self.opener = urllib.request.build_opener(NoRedirect)

    def __call__(self, route, body):
        if route != '/query':
            raise Unknown('only query reads are permitted')
        remaining = self.end - self.clock()
        if remaining <= 0:
            raise Unknown('overall deadline')
        headers = {'Content-Type': 'application/json', 'X-Quipu-Client': 'camayoc-ingress',
                   **planes.provenance_headers('camayoc-ingress')}
        if self.credential:
            headers['Authorization'] = 'Bearer ' + self.credential
        request = urllib.request.Request(self.server + route, json.dumps(body).encode(),
                                         headers, method='POST')
        # A socket inactivity timeout alone permits an endless trickle of bytes.
        # This standalone Unix reader also bounds the entire request. Never
        # replace a caller's active alarm; unsupported/threaded use refuses.
        if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
            raise Unknown('caller already owns the deadline timer')
        def expired(*_):
            raise Unknown('whole request deadline')
        previous = signal.signal(signal.SIGALRM, expired)
        try:
            signal.setitimer(signal.ITIMER_REAL, min(6.0, remaining))
            with self.opener.open(request, timeout=min(6.0, remaining)) as response:
                raw = response.read(MAX_BYTES + 1)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)
        if len(raw) > MAX_BYTES or self.clock() > self.end:
            raise Unknown('response byte or time budget exceeded')
        return json.loads(raw)


def receipt(post, items, board_graph, control, *, clock=time.monotonic):
    start = clock()
    calls = []
    scope = None
    try:
        if (len(items) != 2 or len(set(items)) != 2 or control not in items):
            raise Unknown('exactly two distinct IDs and one in-scope positive control required')
        # Validate IDs and the graph using the canonical native reader rules.
        subjects = {native.item_iri(i) for i in items}
        subjects |= {legacy.BASE_NS + i for i in items}
        board = native.Board(board_graph, items[0].split('-', 1)[0])
        for item in items:
            board.subject(item)
        scope = legacy.graphs((board_graph,))
        if len(scope) != 3:
            raise Unknown('exactly ROOT, observed records and one distinct board required')
        allowed_queries = set(legacy.WORKITEM_QUERIES + legacy.IDENTIFIER_QUERIES)
        values = ' VALUES ?w { ' + ' '.join('<' + s + '>' for s in sorted(subjects)) + ' } '

        def bounded(route, body):
            if route != '/query' or len(calls) >= MAX_REQUESTS or clock() - start >= DEADLINE:
                raise Unknown('read budget exceeded')
            graph = body.get('graph')
            if graph not in scope or set(body) - {'query', 'graph'}:
                raise Unknown('unexpected graph or request fields')
            query = body['query']
            suffix = ' ORDER BY ?w LIMIT 5000 OFFSET 0'
            if not query.endswith(suffix) or query[:-len(suffix)] not in allowed_queries:
                raise Unknown('unexpected or paginated coverage query')
            base = query[:-len(suffix)]
            scoped = base.replace('{', '{' + values, 1) + ' ORDER BY ?w LIMIT 5'
            entry = {'graph': graph, 'operation': 'identifier' if '?id' in base else 'type'}
            calls.append(entry)
            at = clock()
            result = post(route, dict(body, query=scoped))
            elapsed = clock() - at
            entry['seconds'] = elapsed
            if elapsed > 6 or clock() - start > DEADLINE:
                raise Unknown('read deadline exceeded')
            if (not isinstance(result, dict) or 'error' in result or
                    result.get('truncated') is not False or not isinstance(result.get('rows'), list) or
                    len(result['rows']) > 4):
                raise Unknown('malformed, truncated or overfull result')
            rows = result['rows']
            identifier = '?id' in base
            for row in rows:
                if not isinstance(row, dict) or set(row) != ({'w', 'id'} if identifier else {'w'}):
                    raise Unknown('unexpected result columns')
                if row['w'] not in subjects:
                    raise Unknown('out-of-scope subject')
                if identifier and (not isinstance(row['id'], str) or row['id'] not in items):
                    raise Unknown('malformed or conflicting identifier')
                if identifier and row['w'] not in (native.item_iri(row['id']), legacy.BASE_NS + row['id']):
                    raise Unknown('identifier does not own its subject')
            entry['rows'] = len(rows)
            return result

        covered = legacy.covered(bounded, (board_graph,))
        if len(calls) != MAX_REQUESTS or control not in covered:
            raise Unknown('positive control not covered')
        missing = sorted(set(items) - covered)
        return {'status': 'NOT_COVERED' if missing else 'COVERED', 'items': sorted(items),
                'control': control, 'covered': sorted(covered), 'missing': missing,
                'coverage_scope': 'exact-subjects-only', 'subjects': sorted(subjects),
                'graphs': scope, 'requests': len(calls), 'writes': 0, 'calls': calls,
                'seconds': clock() - start}
    except Exception as error:
        # Never expose transport error text, credentials, or an empty coverage set.
        return {'status': 'UNKNOWN', 'error': type(error).__name__, 'requests': len(calls),
                'writes': 0, 'calls': calls, 'seconds': clock() - start}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--enable', action='store_true')
    parser.add_argument('--item', action='append', default=[])
    parser.add_argument('--control')
    parser.add_argument('--board-graph')
    parser.add_argument('--run-id', help='caller supplied identity; does not attest scheduling')
    args = parser.parse_args(argv)
    if not args.enable:
        print(json.dumps({'status': 'OFF', 'requests': 0, 'writes': 0}))
        return 0
    try:
        if not args.run_id or len(args.run_id) > 128 or not args.run_id.isascii() or not args.run_id.isprintable():
            raise Unknown('bounded run identity required')
        hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in
                  (Path(__file__), Path(legacy.__file__), Path(native.__file__),
                   Path(planes.__file__), Path(quipu_auth.__file__))}
        post = Transport(planes.SERVER, quipu_auth.token())
        result = receipt(post, args.item, args.board_graph, args.control)
        result.update(run_id=args.run_id, invocation='caller-declared', at=time.time(),
                      source_sha256_at='before-reads',
                      source_sha256=hashes)
    except Exception as error:
        result = {'status': 'UNKNOWN', 'error': type(error).__name__, 'requests': 0, 'writes': 0}
    print(json.dumps(result, sort_keys=True))
    return {'COVERED': 0, 'NOT_COVERED': 1}.get(result['status'], 2)


if __name__ == '__main__':
    sys.exit(main())
