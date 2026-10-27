#!/usr/bin/env python3
"""Opt-in, read-only two-ID coverage receipt; never installs or arms a schedule.

Uses the legacy covered() semantics through an exact-subject bounded reader.
This receipt is deliberately separate from the global coverage cache.
"""
from __future__ import annotations

import argparse
import hashlib
import datetime
import math
import os
import re
import stat
import contextlib
import urllib.error
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


_UTC_NOW = object()


class UTCWindow:
    """Sealed half-open UTC validity; one local clock source, never a provider."""
    def __init__(self, valid_from, valid_until, *, clock=None):
        self.clock = clock or time.time
        self.start = self.parse(valid_from)
        self.end = self.parse(valid_until)
        if self.start >= self.end:
            raise Unknown('invalid finite UTC order')
        self.last = None

    @staticmethod
    def parse(value):
        if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', value):
            raise Unknown('exact finite UTC bounds required')
        try:
            parsed = datetime.datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=datetime.timezone.utc)
            if parsed.isoformat().replace('+00:00', 'Z') != value:
                raise ValueError
            return parsed.timestamp()
        except (ValueError, OverflowError, OSError):
            raise Unknown('invalid finite UTC bound') from None

    def remaining(self, now=_UTC_NOW):
        now = self.clock() if now is _UTC_NOW else now
        if type(now) not in (int, float) or not math.isfinite(now):
            raise Unknown('finite UTC clock unavailable')
        if self.last is not None and now < self.last:
            raise Unknown('finite UTC clock moved backwards')
        self.last = now
        if now < self.start:
            raise Unknown('finite UTC validity not yet started')
        if now >= self.end:
            raise Unknown('finite UTC validity expired')
        return self.end - now


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Unknown('redirect refused')


class Transport:
    def __init__(self, server, credential, clock=time.monotonic, *, validity=None):
        self.server = server.rstrip('/')
        parsed = urllib.parse.urlsplit(self.server)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname or
                parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path):
            raise Unknown('invalid server origin')
        self.credential = credential
        self.validity = validity
        self.clock = clock
        self.end = clock() + DEADLINE
        self.opener = urllib.request.build_opener(NoRedirect)

    def __call__(self, route, body):
        if route != '/query':
            raise Unknown('only query reads are permitted')
        remaining = self.end - self.clock()
        if self.validity is not None:
            remaining = min(remaining, self.validity.remaining())
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
            if self.validity is not None:
                remaining = min(remaining, self.validity.remaining())
            signal.setitimer(signal.ITIMER_REAL, min(6.0, remaining))
            with self.opener.open(request, timeout=min(6.0, remaining)) as response:
                raw = response.read(MAX_BYTES + 1)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)
        if len(raw) > MAX_BYTES or self.clock() > self.end:
            raise Unknown('response byte or time budget exceeded')
        if self.validity is not None:
            self.validity.remaining()
        self.last_response_bytes = len(raw)
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
        # /query renders references as strings, compacted through the loaded
        # shapes' prefix table. The observed `a:` spelling names BASE_NS.
        # Decode only the exact requested subjects, never an arbitrary prefix
        # or suffix. `w` is constrained by VALUES and occurs as a triple subject;
        # identifier literals in `id` must remain untouched.
        subject_forms = {subject: subject for subject in subjects}
        subject_forms.update({'a:' + i: legacy.BASE_NS + i for i in items})
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
            entry['response_bytes'] = getattr(post, 'last_response_bytes', None)
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
            canonical_rows = []
            for row in rows:
                if not isinstance(row, dict) or set(row) != ({'w', 'id'} if identifier else {'w'}):
                    raise Unknown('unexpected result columns')
                if not isinstance(row['w'], str) or row['w'] not in subject_forms:
                    raise Unknown('out-of-scope subject')
                row = dict(row, w=subject_forms[row['w']])
                if identifier and (not isinstance(row['id'], str) or row['id'] not in items):
                    raise Unknown('malformed or conflicting identifier')
                if identifier and row['w'] not in (native.item_iri(row['id']), legacy.BASE_NS + row['id']):
                    raise Unknown('identifier does not own its subject')
                canonical_rows.append(row)
            entry['rows'] = len(rows)
            return dict(result, rows=canonical_rows)

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


# Qualified snapshot payload/comparator: the same exact two-subject semantics
# as the retained finite collector, with a new explicit private output root.
SNAPSHOT_RECORDS = 'https://camayoc.local/plane/crew/records'
SNAPSHOT_REFS = {native.RDF+'type', native.SCHEMA+'about', native.SCHEMA+'agent',
                 native.SCHEMA+'creator', native.SCHEMA+'isPartOf', native.QUECHUA+'blockedOn',
                 legacy.BASE_NS+'about', legacy.BASE_NS+'observes',
                 'http://www.w3.org/ns/prov#wasGeneratedBy'}


def snapshot_payload(kind, items, board_graph):
    if kind not in ('board', 'records') or len(items) != 2 or len(set(items)) != 2:
        raise Unknown('exact snapshot scope required')
    board = native.Board(board_graph, items[0].split('-', 1)[0])
    for item in items:
        board.subject(item)
    graph, prefix = ((board_graph, native.ITEM) if kind == 'board' else
                     (SNAPSHOT_RECORDS, legacy.BASE_NS))
    subjects = [prefix+i for i in items]
    query = ('SELECT ?w ?p ?v WHERE { VALUES ?w { ' +
             ' '.join('<'+s+'>' for s in subjects) + ' } GRAPH <'+graph+
             '> { ?w ?p ?v } } LIMIT 257')
    return json.dumps({'query': query}).encode()


def snapshot_canonical(rows, kind, items):
    prefixes = dict(native.PREFIXES, a=legacy.BASE_NS,
                    prov='http://www.w3.org/ns/prov#', xsd='http://www.w3.org/2001/XMLSchema#')
    def iri(value):
        if not isinstance(value, str):
            raise Unknown('snapshot IRI not string')
        prefix, sep, tail = value.partition(':')
        if sep and prefix in prefixes:
            return prefixes[prefix]+tail
        if value.startswith(('https://', 'http://', 'urn:')):
            return value
        raise Unknown('unknown snapshot IRI representation')
    prefix = native.ITEM if kind == 'board' else legacy.BASE_NS
    allowed = {prefix+i for i in items}
    out = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'w', 'p', 'v'}:
            raise Unknown('snapshot columns')
        w, predicate, value = iri(row['w']), iri(row['p']), row['v']
        if w not in allowed:
            raise Unknown('foreign snapshot subject')
        if predicate in SNAPSHOT_REFS:
            value = iri(value)
        elif isinstance(value, dict):
            value = dict(value)
            if 'datatype' in value:
                value['datatype'] = iri(value['datatype'])
        out.append({'w': w, 'p': predicate, 'v': value})
    return sorted(out, key=lambda x: json.dumps(x, sort_keys=True, separators=(',', ':')))


def snapshot_semantics(board, records, items, control):
    def vals(rows, w, p):
        return [r['v'] for r in rows if r['w'] == w and r['p'] == p]
    def require(value):
        if not value:
            raise Unknown('snapshot identity or provenance failed')
    for item in items:
        w = native.ITEM+item
        require(vals(board, w, native.SCHEMA+'identifier') == [item])
        require(native.SCHEMA+'Action' in vals(board, w, native.RDF+'type'))
        require('declared' in vals(board, w, native.QUECHUA+'sourceKind'))
        require(any(isinstance(x, str) and x.startswith(native.PRINCIPAL)
                    for x in vals(board, w, native.SCHEMA+'creator')))
    w = legacy.BASE_NS+control
    require(vals(records, w, legacy.BASE_NS+'identifier') == [control])
    require(legacy.BASE_NS+'WorkItem' in vals(records, w, native.RDF+'type'))
    require('observed' in vals(records, w, legacy.BASE_NS+'sourceKind'))
    require(native.ITEM+control in vals(records, w, legacy.BASE_NS+'about'))
    require(any(isinstance(x, str) and x.startswith(legacy.BASE_NS+'episode_tracker-work-item_'+control+'_')
                for x in vals(records, w, 'http://www.w3.org/ns/prov#wasGeneratedBy')))


@contextlib.contextmanager
def snapshot_directory(path, *, create=False):
    """Walk directory descriptors without following any symlink component."""
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts or path == Path('/'):
        raise Unknown('absolute private snapshot directory required')
    with contextlib.ExitStack() as stack:
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        stack.callback(os.close, fd)
        for index, part in enumerate(path.parts[1:]):
            final = index == len(path.parts[1:])-1
            if final and create:
                parent = os.fstat(fd)
                if parent.st_uid != os.getuid() or parent.st_mode & 0o077:
                    raise Unknown('private snapshot parent required')
                os.mkdir(part, 0o700, dir_fd=fd)  # collision is terminal
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            stack.callback(os.close, next_fd)
            fd = next_fd
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise Unknown('private snapshot directory required')
        yield fd


def snapshot_save(fd, name, data):
    if '/' in name or name in ('.', '..'):
        raise Unknown('fixed snapshot filename required')
    out = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    with os.fdopen(out, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.fsync(fd)


def snapshot_json(fd, name, value):
    snapshot_save(fd, name, json.dumps(value, sort_keys=True).encode()+b'\n')


def snapshot_read(fd, name):
    stream_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(stream_fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise Unknown('private regular snapshot evidence required')
        raw = stream.read(1024*1024+1)
    if len(raw) > 1024*1024:
        raise Unknown('snapshot evidence too large')
    return json.loads(raw)


class SnapshotTransport:
    """One bounded request; fixed metadata, never request headers or error text."""
    def __init__(self, server, credential, validity, *, seconds=6.0):
        # Reuse origin validation and redirect refusal, never a second request.
        self.reader = Transport(server, credential, validity=validity)
        self.validity = validity
        self.seconds = min(6.0, seconds)

    def __call__(self, payload):
        remaining = min(self.seconds, self.validity.remaining())
        if remaining <= 0:
            raise Unknown('snapshot deadline')
        headers = {'Content-Type': 'application/json', 'X-Quipu-Client': 'camayoc-ingress',
                   **planes.provenance_headers('camayoc-ingress')}
        if self.reader.credential:
            headers['Authorization'] = 'Bearer '+self.reader.credential
        request = urllib.request.Request(self.reader.server+'/query', payload, headers, method='POST')
        if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
            raise Unknown('caller already owns deadline')
        def expired(*_):
            raise Unknown('whole snapshot deadline')
        previous = signal.signal(signal.SIGALRM, expired)
        raw = b''
        try:
            remaining = min(remaining, self.validity.remaining())
            signal.setitimer(signal.ITIMER_REAL, remaining)
            try:
                response = self.reader.opener.open(request, timeout=remaining)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                raw = response.read(MAX_BYTES)
                summary = {k: response.headers[k] for k in ('Content-Type', 'Content-Length')
                           if k in response.headers}
                length = response.headers.get('Content-Length')
                incomplete = len(raw) == MAX_BYTES
                if length and length.isdecimal():
                    incomplete = incomplete or int(length) != len(raw)
                self.validity.remaining()
                return raw, {'http_status': response.code, 'incomplete': incomplete,
                             'header_summary': summary}
        except Exception as error:
            error.partial_body = raw[:MAX_BYTES]
            raise
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)


def snapshot_phase(plan, manifest_sha, root, phase, *, request_factory=None,
                   clock=time.monotonic, utc_clock=None):
    """At most two immutable attempts per phase; AFTER needs this exact BEFORE."""
    if phase not in ('before', 'after'):
        raise Unknown('snapshot phase required')
    validity = UTCWindow(plan['valid_from'], plan['valid_until'], clock=utc_clock)
    validity.remaining()
    items, control, graph = plan['items'], plan['control'], plan['board_graph']
    if control != items[0]:
        raise Unknown('first snapshot control required')
    payloads = {kind: snapshot_payload(kind, items, graph) for kind in ('board', 'records')}
    scope = {'format': 'camayoc-exact-snapshot-v1', 'manifest_sha256': manifest_sha,
             'items': items, 'control': control, 'board_graph': graph,
             'source_sha256': plan['source_sha256']}
    with snapshot_directory(root, create=phase == 'before') as fd:
        inode = os.fstat(fd)
        scope['root_identity'] = [inode.st_dev, inode.st_ino]
        if phase == 'before':
            snapshot_json(fd, 'scope.json', scope)
            previous_seconds = 0.0
        else:
            if snapshot_read(fd, 'scope.json') != scope:
                raise Unknown('snapshot scope or root changed')
            previous = snapshot_read(fd, 'before-result.json')
            if previous.get('status') != 'PASS':
                raise Unknown('complete successful BEFORE required')
            previous_seconds = previous.get('seconds')
            if (type(previous_seconds) not in (int, float) or not math.isfinite(previous_seconds) or
                    not 0 <= previous_seconds <= 24):
                raise Unknown('snapshot prior budget unknown')
        # Exclusive phase claim before all requests: failures cannot be retried.
        snapshot_json(fd, phase+'-attempt.json', {'state': 'consumed-before-query'})
        calls, canonical = [], {}
        start = clock()
        result = {'status': 'UNKNOWN', 'requests_attempted': 0, 'writes': 0}
        try:
            for kind, payload in payloads.items():
                validity.remaining()
                remaining = 24 - previous_seconds - (clock()-start)
                if remaining <= 0:
                    raise Unknown('snapshot total budget')
                name = phase+'-'+kind
                snapshot_save(fd, name+'-payload.json', payload)
                attempt = {'payload_sha256': hashlib.sha256(payload).hexdigest(), 'requests_attempted': 1}
                snapshot_json(fd, name+'-attempt.json', attempt)
                calls.append(attempt)
                at = clock()
                raw, meta = b'', {'http_status': None, 'incomplete': True, 'transport_error': None}
                try:
                    request = (request_factory if request_factory is not None else
                               lambda seconds: SnapshotTransport(plan['server'], quipu_auth.token(), validity,
                                                                 seconds=seconds))(min(6.0, remaining))
                    validity.remaining()
                    raw, details = request(payload)
                    if not isinstance(raw, bytes) or not isinstance(details, dict):
                        raise Unknown('snapshot transport shape')
                    meta.update({key: details[key] for key in ('http_status', 'incomplete', 'header_summary')
                                 if key in details})
                except Exception as error:
                    raw = getattr(error, 'partial_body', b'')[:MAX_BYTES]
                    meta['transport_error'] = type(error).__name__
                if not isinstance(raw, bytes):
                    raw = b''
                    meta['transport_error'] = 'InvalidBody'
                elapsed = clock()-at
                meta.update(elapsed_seconds=elapsed, response_bytes_retained=len(raw))
                snapshot_save(fd, name+'-response.bin', raw[:MAX_BYTES])
                snapshot_json(fd, name+'-response-meta.json', meta)  # before any assertion
                attempt.update(seconds=elapsed, response_bytes_retained=len(raw), http_status=meta['http_status'])
                checks = {}
                checks['transport_ok'] = meta['transport_error'] is None
                checks['http_success'] = type(meta['http_status']) is int and 200 <= meta['http_status'] < 300
                checks['complete_bounded_body'] = meta['incomplete'] is False and len(raw) <= MAX_BYTES
                try:
                    data = json.loads(raw)
                    checks['json_parse'] = True
                except (ValueError, UnicodeError):
                    data = None
                    checks['json_parse'] = False
                checks['json_object'] = isinstance(data, dict)
                checks['truncated_false'] = isinstance(data, dict) and data.get('truncated') is False
                checks['rows_list'] = isinstance(data, dict) and isinstance(data.get('rows'), list)
                checks['row_count_lt_257'] = checks['rows_list'] and len(data['rows']) < 257
                checks['error_absent'] = isinstance(data, dict) and 'error' not in data
                checks['request_seconds_lte_6'] = elapsed <= 6
                checks['combined_seconds_lte_24'] = previous_seconds+clock()-start <= 24
                try:
                    validity.remaining()
                    checks['utc_valid'] = True
                except Unknown:
                    checks['utc_valid'] = False
                failed = [key for key, value in checks.items() if not value]
                snapshot_json(fd, name+'-validation.json', {'checks': checks, 'failed_predicates': failed})
                if failed:
                    raise Unknown('bounded snapshot response failed')
                canonical[kind] = snapshot_canonical(data['rows'], kind, items)
                snapshot_json(fd, name+'-canonical.json', canonical[kind])
            snapshot_semantics(canonical['board'], canonical['records'], items, control)
            if phase == 'after':
                equal = {kind: snapshot_read(fd, 'before-'+kind+'-canonical.json') == rows
                         for kind, rows in canonical.items()}
                snapshot_json(fd, 'full-row-equality.json', equal)
                if not all(equal.values()):
                    raise Unknown('snapshot full rowsets differ')
            validity.remaining()
            if previous_seconds + clock()-start > 24:
                raise Unknown('snapshot total budget')
            result['status'] = 'PASS'
        except Exception as error:
            result['error'] = type(error).__name__
        result.update(requests_attempted=len(calls), calls=calls,
                      seconds=previous_seconds+clock()-start)
        snapshot_json(fd, phase+'-result.json', result)
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--enable', action='store_true')
    parser.add_argument('--item', action='append', default=[])
    parser.add_argument('--control')
    parser.add_argument('--board-graph')
    parser.add_argument('--snapshot-root', type=Path)
    parser.add_argument('--snapshot-phase', choices=('before', 'after'))
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--manifest-sha256')
    parser.add_argument('--valid-from')
    parser.add_argument('--valid-until')
    parser.add_argument('--run-id', help='caller supplied identity; does not attest scheduling')
    args = parser.parse_args(argv)
    if not args.enable:
        print(json.dumps({'status': 'OFF', 'requests': 0, 'writes': 0}))
        return 0
    try:
        if args.snapshot_phase:
            import scheduled_exact_coverage as schedule
            if not args.manifest or not args.manifest.is_absolute() or not args.snapshot_root:
                raise Unknown('sealed manifest and fresh snapshot root required')
            plan = schedule.load_plan(args.manifest, args.manifest_sha256)
            UTCWindow(plan['valid_from'], plan['valid_until']).remaining()
            schedule.source_receipt(plan)
            result = snapshot_phase(plan, args.manifest_sha256, args.snapshot_root, args.snapshot_phase)
            print(json.dumps(result, sort_keys=True))
            return 0 if result['status'] == 'PASS' else 2
        if args.snapshot_root or args.manifest or args.manifest_sha256:
            raise Unknown('snapshot arguments require explicit phase')
        validity = UTCWindow(args.valid_from, args.valid_until)
        validity.remaining()
        if not args.run_id or len(args.run_id) > 128 or not args.run_id.isascii() or not args.run_id.isprintable():
            raise Unknown('bounded run identity required')
        hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in
                  (Path(__file__), Path(legacy.__file__), Path(native.__file__),
                   Path(planes.__file__), Path(quipu_auth.__file__))}
        validity.remaining()
        credential = quipu_auth.token()
        validity.remaining()
        post = Transport(planes.SERVER, credential, validity=validity)
        result = receipt(post, args.item, args.board_graph, args.control)
        result.update(run_id=args.run_id, invocation='caller-declared', at=time.time(),
                      source_sha256_at='before-reads',
                      source_sha256=hashes)
    except Exception as error:
        result = {'status': 'UNKNOWN', 'error': type(error).__name__, 'requests': 0, 'writes': 0}
    result['server'] = planes.SERVER
    print(json.dumps(result, sort_keys=True))
    return {'COVERED': 0, 'NOT_COVERED': 1}.get(result['status'], 2)


if __name__ == '__main__':
    sys.exit(main())
