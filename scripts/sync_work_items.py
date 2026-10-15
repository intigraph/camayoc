#!/usr/bin/env python3
"""Bounded standing delivery of current authoritative br WorkItems.

One record by default, with reviewed expiring opt-in batches up to four. Each
record has at most one write and two reads per tick. The scheduler supplies an
explicit tracker database; this adapter never reads cost attribution.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
from pathlib import Path
import subprocess
import time

import camayoc_metrics
from ingest_work_items import BASE_NS, _entity_name, episode_for
import planes

INTERVAL = 60
# 24h, not 6h (aegis-7dcleu, malcolm's call). At one item per INTERVAL the lane
# serves ~60/h shared with transitions; 645 items at 6h needed ~107/h, so rechecks
# lagged without bound (31h measured). 645 / 24h is ~27/h, ~45% utilization.
RECHECK = 24 * 3600
MAX_BODY = 256 * 1024
MAX_ATTEMPTS = 3
MAX_BATCH = 4
MAX_TRIAL_SECONDS = 3600


def batch_policy(path, now):
    """An absent/expired opt-in policy keeps the one-record production budget.

    The review reference is an audit pointer, not an authorization mechanism.
    Operators must obtain review before installing this local, expiring policy.
    """
    try:
        policy = json.loads(path.read_text())
    except FileNotFoundError:
        return 1, None
    if not isinstance(policy, dict):
        raise ValueError('invalid batch policy')
    limit = policy.get('max_items')
    start, end = policy.get('starts_at'), policy.get('expires_at')
    review = policy.get('review')
    if (type(limit) is not int or not 1 <= limit <= MAX_BATCH
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in (start, end))
            or not 0 <= start < end <= start + MAX_TRIAL_SECONDS
            or not isinstance(review, str) or not review.strip() or len(review) > 128):
        raise ValueError('invalid batch policy')
    return (limit if start <= now < end else 1), policy


def save(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, sort_keys=True) + '\n')
    temporary.replace(path)


def records_from(payload):
    """Accept both public br list formats, never treat truncation as complete."""
    if isinstance(payload, dict):
        if payload.get('has_more') is not False or payload.get('offset') != 0:
            raise ValueError('tracker list is incomplete')
        records = payload.get('issues')
        if not isinstance(records, list) or payload.get('total') != len(records):
            raise ValueError('tracker list total is unproven')
    else:
        records = payload
    if not isinstance(records, list) or any(not isinstance(r, dict) for r in records):
        raise ValueError('tracker list must contain records')
    if len({r['id'] for r in records}) != len(records):
        raise ValueError('duplicate tracker identifiers')
    return [r for r in records if r.get('status') == 'in_progress' or
            (r.get('status') in ('open', 'blocked', 'deferred') and r.get('assignee'))]


#: A tracker read waits on the store lock, and under a crew write burst that wait
#: is long: measured 2026-10-06 23:4xZ, the current-work read took up to 64.5 s
#: while about 10 br processes queued on the store (aegis-ky3zpa). At 15 s every
#: minute of a burst ended UNKNOWN. The run lock already turns an overrun into
#: BUSY on the next tick, so a bound above the measured tail costs nothing but
#: latency. A read that exceeds it is still UNKNOWN, never silently partial.
BR_READ_TIMEOUT = 90


def fetch_ids(db, ids, run=None):
    """Tracker records for these ids, closed ones included, in bounded chunks."""
    run = run or subprocess.run
    out = []
    ids = sorted(ids)
    for start in range(0, len(ids), 100):
        argv = ['br', '--db', str(db), 'list', '--all', '--limit', '0', '--json']
        for item in ids[start:start + 100]:
            argv += ['--id', item]
        result = run(argv, check=True, capture_output=True, text=True, timeout=BR_READ_TIMEOUT)
        payload = json.loads(result.stdout)
        records = payload.get('issues') if isinstance(payload, dict) else payload
        if not isinstance(records, list):
            raise ValueError('tracker id lookup returned no records')
        out += [r for r in records if isinstance(r, dict)]
    return out


def collect(db, tracked=(), run=None, deps_cache=None):
    """Current work, plus the records whose TRANSITIONS it depends on (aegis-3b3nrb).

    Current work alone never observes a close: a bead leaves the current set at
    the moment it closes, so its last Observation said open forever and
    everything blocked on it read BLOCKED (measured on aegis-c6wun4, closed
    2026-09-24 04:16Z, still "open" in the graph 36 h later). Two more sets are
    fetched and marked `trailing`: every `blocks` target of current work,
    whatever its status or assignee, and every tracked item that has left the
    current set, until its final state is verified.
    """
    run = run or subprocess.run
    # `--status deferred` explicitly: in this br, `--deferred` beside explicit
    # --status filters adds NO deferred rows (aegis-prhnn4, measured 0 of 156).
    result = run(['br', '--db', str(db), 'list', '--status', 'open',
                  '--status', 'in_progress', '--status', 'blocked',
                  '--status', 'deferred', '--deferred', '--limit', '0', '--json'],
                 check=True, capture_output=True, text=True, timeout=BR_READ_TIMEOUT)
    records = records_from(json.loads(result.stdout))
    # Same dependency set the backfill projects (aegis-3b3nrb), so both writers
    # mint the SAME Observation version and never two competing "latest" ones.
    have = {r['id'] for r in records}
    attach_deps(records, db, deps_cache)
    want = set(tracked) | {t for r in records for t in r.get('blocked_on') or []}
    extra = [r for r in fetch_ids(db, want - have, run=run) if r.get('id') not in have]
    for record in extra:
        record['trailing'] = True
    attach_deps(extra, db, deps_cache)
    return records + extra


def attach_deps(records, db, cache=None):
    """attach_blocked_on, looking up only records whose updated_at moved.

    br bumps a record's updated_at on `dep add` and `dep remove` (measured on
    two scratch beads, 2026-09-25), so an unchanged updated_at means an
    unchanged dependency set. One lookup costs ~0.34 s; re-reading all of them
    every minute cost 33 s of each 60 s tick.
    """
    from ingest_work_items import attach_blocked_on
    if cache is None:
        attach_blocked_on(records, db)
        return
    stale = []
    for record in records:
        if not record.get('dependency_count'):
            continue
        hit = cache.get(record['id'])
        if hit and hit[0] == record.get('updated_at'):
            record['blocked_on'] = list(hit[1])
        else:
            stale.append(record)
    attach_blocked_on(stale, db)
    for record in stale:
        if not record.get('dep_unknown'):
            cache[record['id']] = [record.get('updated_at'), record.get('blocked_on') or []]


def http_status(exc):
    """The HTTP status behind a failed quipu call, or None. Only the code is
    kept: response bodies and URLs are never recorded."""
    cause = getattr(exc, '__cause__', None)
    code = getattr(cause, 'code', None) or getattr(exc, 'code', None)
    return code if isinstance(code, int) and 100 <= code <= 599 else None


def readback(body, post):
    """A control plus exact direct assertions, always scoped to observed records."""
    graph = body['graph']
    control = post('/query', {'graph': graph, 'query':
        f'SELECT ?s WHERE {{ ?s a ?t . FILTER(?t = <{BASE_NS}WorkItem>) }} LIMIT 1'})
    if not isinstance(control.get('rows'), list) or not control['rows']:
        raise ValueError('WorkItem control unproven')
    item, observation = body['nodes']
    identifier = json.dumps(item['properties']['identifier'])
    value = json.dumps(observation['properties']['observedValue'])
    query = (f'SELECT ?t ?o WHERE {{ <{BASE_NS}{item["name"]}> a ?t ; '
             f'<{BASE_NS}identifier> {identifier} ; <{BASE_NS}sourceKind> "observed" ; '
             f'<{BASE_NS}observes> <{BASE_NS}{observation["name"]}> . '
             f'<{BASE_NS}{observation["name"]}> a ?o ; <{BASE_NS}observedValue> {value} . '
             f'FILTER(?t = <{BASE_NS}WorkItem> && ?o = <{BASE_NS}Observation>) }}')
    found = post('/query', {'graph': graph, 'query': query})
    if not isinstance(found.get('rows'), list) or found.get('truncated'):
        raise ValueError('WorkItem readback unproven')
    return bool(found['rows'])


def recovery_metrics(entries, now):
    """Recovery clocks belong to retained bodies, not the delivery backlog."""
    pending = [entry['pending'] for entry in entries.values() if entry.get('pending')]
    starts = [entry.get('started_at') for entry in pending]
    attempts = [entry.get('attempts') for entry in pending]
    return {
        'indeterminate': len(pending),
        # Legacy/invalid clocks are unknown; never fabricate their first write
        # time or inherit hours spent waiting BEFORE recovery from due_since.
        'pending_oldest_seconds': (
            -1 if any(type(start) not in (int, float) or not 0 <= start <= now
                      or not math.isfinite(start) for start in starts)
            else max((now - start for start in starts), default=0)),
        'exhausted_retries': (
            -1 if any(type(attempt) is not int or attempt < 0 for attempt in attempts)
            else sum(attempt >= MAX_ATTEMPTS for attempt in attempts)),
    }


def tick(records, state, path, *, actor, source, now, post, max_items=1):
    if type(max_items) is not int or not 1 <= max_items <= MAX_BATCH:
        raise ValueError('invalid ingress item budget')
    if now < state.get('next_tick', 0):
        # Even a backoff publishes fresh metrics. A cached age could otherwise
        # hide a stuck pending body while next_tick is still in the future.
        return {**state.get('receipt', {'status': 'UNKNOWN'}),
                **recovery_metrics(state.get('items', {}), now), 'mode': 'BACKOFF',
                'requests': 0, 'writes': 0, 'verified': 0, 'items': [], 'max_items': max_items}
    state['next_tick'] = now + INTERVAL
    entries = state.setdefault('items', {})
    # Preserve pending bodies even when a task closes or its tracker fields change.
    # They are immutable retry evidence, not a cache that can be overwritten.
    current = {}
    invalid = []
    for record in records:
        if record.get('dep_unknown'):
            # Its dependencies could not be read: writing it now would record
            # "blocks on nothing". Park it; the next tick retries the lookup.
            invalid.append({'id': str(record.get('id', 'UNKNOWN'))[:120],
                            'error': 'DependencyLookupFailed'})
            continue
        try:
            item = _entity_name(record['id'])
            body = episode_for(record, actor=actor, source=f'{source}#{item}')
            if len(json.dumps(body, sort_keys=True).encode()) > MAX_BODY:
                raise ValueError('tracker episode exceeds byte cap')
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            invalid.append({'id': str(record.get('id', 'UNKNOWN'))[:120],
                            'error': type(exc).__name__})
            continue
        current[item] = body
        entry = entries.setdefault(item, {'first_seen': now, 'last_attempt': 0})
        if entry.get('version') != body['name'] and 'due_since' not in entry:
            entry['due_since'] = now
        entry['priority'] = record.get('priority', 4)
        entry['active'] = record.get('status') == 'in_progress'
        entry['trailing'] = bool(record.get('trailing'))
        entry['closed'] = record.get('status') == 'closed'
        if not entry['trailing']:
            entry.pop('final', None)  # back in current work: follow it again
        entry['created_at'] = record['created_at']
    candidates = []
    for item, entry in entries.items():
        if item not in current and not entry.get('pending'):
            continue
        owed = bool(entry.get('pending') or entry.get('error')
                    or entry.get('version') != current[item]['name'])
        due = owed or now - entry.get('verified_at', 0) >= RECHECK
        # due_since measures OWED work only (backlog age, the Unhealthy signal). A
        # periodic recheck is lowest-priority rotation and may legitimately wait
        # many hours; stamping it made a recheck that later timed out carry its
        # whole rotation wait into oldest_seconds and page (31h, 2026-10-01,
        # aegis-64cr5o). Its clock starts when it becomes owed (error / pending).
        if owed:
            entry.setdefault('due_since', now)
        else:
            entry.pop('due_since', None)
        if due and now >= entry.get('not_before', 0):
            candidates.append(item)
    receipt = {'status': 'OK', 'requests': 0, 'writes': 0, 'verified': 0,
               'current': len(current), 'invalid': len(invalid), 'parked': invalid[:20],
               'items': [], 'max_items': max_items}
    item_requests = 0
    def request(endpoint, body):
        nonlocal item_requests
        if item_requests >= 3 or receipt['requests'] >= 3 * max_items:
            raise ValueError('ingress request budget exhausted')
        item_requests += 1
        receipt['requests'] += 1
        return post(endpoint, body)
    # Fairness starts a new arrival's clock at first_seen, not at the sentinel
    # last_attempt=0. Otherwise every new arrival outranks an existing item's
    # unverified transition forever. Attempts advance the clock, so failed
    # records rotate behind work already waiting. Current claims win ties.
    candidates.sort(key=lambda i: entries[i].get('created_at', ''), reverse=True)
    # A version change (a real transition) goes ahead of the periodic recheck
    # rotation; otherwise it waited behind every current item at one per tick.
    # A recheck that FAILED is owed work too, not rotation: its error keeps the
    # whole receipt UNKNOWN until it is retried, and behind a standing backlog
    # of transitions it was never retried (aegis-alfe2l: 629 ticks, 0 attempts).
    # The fair clock below still rotates it behind work already waiting.
    candidates.sort(key=lambda i: (not (entries[i].get('pending')
                                        or entries[i].get('error')
                                        or entries[i].get('version') != current[i]['name']),
                                   max(entries[i]['last_attempt'], entries[i]['first_seen']),
                                   entries[i]['last_attempt'] != 0,
                                   not entries[i].get('active'),
                                   entries[i].get('priority', 4)))
    # Reconcile indeterminate writes ahead of the fresh backlog. Alternate
    # recovery preference with the fair queue so an unreachable read control
    # cannot starve new work. A retry write still needs two absent reads.
    recovery = [i for i in candidates if entries[i].get('pending')
                and entries[i]['pending']['attempts'] < MAX_ATTEMPTS]
    prefer_recovery = bool(recovery) and not state.get('last_was_recovery', False)
    if prefer_recovery:
        candidates.remove(recovery[0])
        candidates.insert(0, recovery[0])
    state['last_was_recovery'] = prefer_recovery
    for item in candidates[:max_items]:
        item_requests = 0
        receipt['items'].append(item)
        entry = entries[item]
        receipt['item'] = item
        entry['last_attempt'] = now
        pending = entry.get('pending')
        try:
            if pending:
                # No repeat write until two separately scheduled, controlled
                # absent reads. Never replace this body with a fresher record.
                if pending.get('absent_reads', 0) >= 2 and pending['attempts'] < MAX_ATTEMPTS:
                    pending['attempts'] += 1
                    pending['absent_reads'] = 0
                    save(path, state)
                    receipt['writes'] += 1
                    response = request('/episode', pending['body'])
                    entry['write_receipt'] = {k: response.get(k) for k in ('outcome', 'tx_id', 'count')}
                    if response.get('outcome') not in ('created', 'updated', 'unchanged'):
                        raise ValueError('episode outcome unproven')
                if not readback(pending['body'], request):
                    pending['absent_reads'] = pending.get('absent_reads', 0) + 1
                    raise ValueError('pending tracker episode not readable')
            else:
                body = current[item]
                # An unchanged version needs only a read, including recovery
                # after legitimate retraction. Re-emission uses the same body.
                if entry.get('version') == body['name'] and readback(body, request):
                    entry['verified_at'] = now
                    entry.pop('due_since', None)
                    receipt['verified'] += 1
                else:
                    pending = {'body': body, 'attempts': 0, 'absent_reads': 0,
                               'started_at': now}
                    entry['pending'] = pending
                    # A failed periodic recheck used two requests: leave the
                    # exact pending body for later, without a same-tick write.
                    if item_requests == 0:
                        pending['attempts'] = 1
                        save(path, state)
                        receipt['writes'] += 1
                        response = request('/episode', body)
                        entry['write_receipt'] = {k: response.get(k) for k in ('outcome', 'tx_id', 'count')}
                        if response.get('outcome') not in ('created', 'updated', 'unchanged'):
                            raise ValueError('episode outcome unproven')
                        if not readback(body, request):
                            raise ValueError('tracker episode not readable after write')
                    else:
                        raise ValueError('previously verified tracker episode no longer readable')
            if pending:
                entry['version'] = pending['body']['name']
                entry['verified_at'] = now
                entry.pop('pending', None)
                entry.pop('due_since', None)
                receipt['verified'] += 1
            if entry.get('trailing') and entry.get('closed'):
                entry['final'] = True  # its last transition is in the graph
            entry.pop('error', None)
            entry.pop('error_status', None)
            entry.pop('not_before', None)
        except Exception as exc:
            # Do not print transport response bodies or credential-bearing URLs.
            entry['error'] = type(exc).__name__
            # The HTTP status alone is safe and separates a refusal (4xx: a
            # byte-identical retry cannot succeed) from a transient failure.
            status = http_status(exc)
            if status:
                entry['error_status'] = status
                receipt['error_status'] = status
            else:
                entry.pop('error_status', None)
            entry.setdefault('due_since', now)  # owed from now (see the candidate loop)
            entry['not_before'] = now + (900 if entry.get('pending', {}).get('attempts', 0) >= MAX_ATTEMPTS else INTERVAL)
            receipt.update(status='UNKNOWN', error=type(exc).__name__)
    outstanding = [e for i, e in entries.items() if e.get('pending') or
                   (i in current and (e.get('version') != current[i]['name'] or e.get('error')))]
    receipt['backlog'] = len(outstanding)
    receipt['oldest_seconds'] = max((now - e.get('due_since', now) for e in outstanding), default=0)
    # Recheck staleness, invisible since due_since counts owed work only (camayoc#59):
    # the oldest verification among current items, and the recheck demand as a
    # fraction of lane capacity (> 1 means rechecks can never keep up).
    receipt['oldest_verified_seconds'] = max(
        (now - entries[i]['verified_at'] for i in current if entries.get(i, {}).get('verified_at')),
        default=0)
    receipt['recheck_utilization'] = round((len(current) / (RECHECK / 3600)) / (3600 / INTERVAL), 4)
    # oldest_verified_seconds skips never-verified items and reads 0 when NONE are
    # verified, so a warning on it alone is blind to zero coverage (malcolm, review
    # of #60). Count the current items that have never been verified.
    receipt['unverified_items'] = sum(1 for i in current if not entries.get(i, {}).get('verified_at'))
    pending_entries = [e for e in outstanding if e.get('pending')]
    receipt.update(recovery_metrics(entries, now))
    if (invalid or any(e['pending']['attempts'] >= MAX_ATTEMPTS for e in pending_entries)
            or any(e.get('error') and not e.get('pending') for e in outstanding)
            or (pending_entries and not receipt['verified'])
            or receipt['oldest_seconds'] > max(900, len(current) * INTERVAL * 2)):
        receipt['status'] = 'UNKNOWN'
    elif pending_entries:
        receipt['status'] = 'DEGRADED'
    # Retain all indeterminate writes; prune inactive confirmed entries only.
    state['items'] = {i: e for i, e in entries.items() if i in current or e.get('pending')
                      or now - e.get('verified_at', 0) < RECHECK}
    state['receipt'] = receipt
    save(path, state)
    return receipt


def native_main(args):
    """Protected selector input, dedicated custody, bounded one-record tick.

    Existing br JSON state cannot become native custody. A new cursor must be
    initialized under its separate review, so an uninitialized schedule fails
    UNKNOWN instead of silently replaying history or skipping to a watermark.
    """
    import sys
    import seed_work_items as native
    import seed_change_custody as custody
    import seed_work_item_sync as native_sync
    import urllib.parse
    import urllib.request
    args.state.parent.mkdir(parents=True,exist_ok=True)
    started=time.time()
    receipt={'status':'UNKNOWN','requests':0,'writes':0,'verified':0}
    # aegis-ksd (wu 10-15 ruling): freshness is measured from the last SUCCESSFUL
    # verified receipt, kept apart from the attempt time. A failed tick carries
    # the previous success forward; it never manufactures a fresh one.
    last_success=None
    graph=None
    with args.state.with_suffix('.lock').open('a') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({'status':'BUSY','requests':0,'writes':0}));return 0
        # Read the carried success first thing UNDER the lock, before anything
        # that can fail: an early failure (authority, custody, journal) must not
        # save None over a real success, and no concurrent tick can save between
        # this read and ours (ian, camayoc#127 review).
        try:
            carried=json.loads(args.state.with_suffix('.receipt.json').read_text()).get('last_success_at')
            if type(carried) in (int,float) and math.isfinite(carried):
                last_success=carried
        except (OSError,ValueError,AttributeError):
            pass
        journal=None
        try:
            raw=sys.stdin.buffer.read(4097)
            if len(raw)>4096:raise native.NativeUnknown('authority projection exceeds budget')
            authority=json.loads(raw)
            reader=native.from_authority(lambda *args:None,authority,server=planes.SERVER)
            graph=reader.board.graph
            if not str(args.state).endswith('.sqlite3'):
                raise custody.CustodyUnknown('native custody requires a separate .sqlite3 state; no br migration')
            journal=custody.Journal(args.state,reader.board,server=planes.SERVER,initial_tx=args.initial_tx)
            last_path=args.state.with_suffix('.receipt.json')
            if last_path.exists():
                previous=json.loads(last_path.read_text())
                previous_time=previous.get('attempted_at')
                if type(previous_time) not in (int,float) or not math.isfinite(previous_time):
                    raise custody.CustodyUnknown('last native tick timestamp unproved')
                if started<previous_time+INTERVAL:
                    print(json.dumps({'status':'BUSY','requests':0,'writes':0}));return 0
            def post(endpoint,body):
                return planes._post(endpoint,body,client='camayoc-ingress')
            def changes(**params):
                # /changes is GET, with an explicit graph and whole-tx cursor.
                request=urllib.request.Request(planes.SERVER+'/changes?'+urllib.parse.urlencode(params),
                    headers={'X-Quipu-Client':'camayoc-ingress',**planes.provenance_headers('camayoc-ingress')})
                with urllib.request.urlopen(request,timeout=15) as response:
                    payload=response.read(custody.MAX_BYTES+1)
                if len(payload)>custody.MAX_BYTES:raise custody.CustodyUnknown('feed exceeds response budget')
                return json.loads(payload)
            receipt=native_sync.tick(reader,journal,post,changes=changes,
                    plane=planes.plane_for('observed'),actor=args.actor)
        except Exception as exc:
            receipt['error']=type(exc).__name__  # never log auth or payloads
        finally:
            if journal is not None:journal.db.close()
        if receipt['status']=='OK':
            last_success=started
        save(args.state.with_suffix('.receipt.json'),
             {'attempted_at':started,'last_success_at':last_success,**receipt})
    print(json.dumps(receipt,sort_keys=True))
    code=0 if receipt['status']=='OK' else 2
    if args.publish_status:
        samples=[('camayoc_workitem_ingress_timestamp_seconds',{},started),
                 ('camayoc_workitem_ingress_exit_status',{},code),
                 ('camayoc_workitem_ingress_backlog',{},receipt.get('backlog',-1)),
                 ('camayoc_workitem_ingress_indeterminate',{},int(code!=0)),
                 ('camayoc_workitem_ingress_requests',{},receipt.get('requests',0)),
                 ('camayoc_workitem_ingress_writes',{},receipt.get('writes',0)),
                 ('camayoc_workitem_ingress_verified',{},receipt.get('verified',0))]
        # Never a success time that did not happen: absent until the first success.
        if last_success is not None:
            samples.append(('camayoc_workitem_ingress_last_success_timestamp_seconds',{},last_success))
        if type(receipt.get('cursor')) is int:
            samples.append(('camayoc_workitem_ingress_cursor',{},receipt['cursor']))
        # Coverage is disclosed, not implied: the feed reads ONE board graph (main);
        # ephemeral and pre-cutover history are not covered (wu ruling 2).
        if graph:
            samples.append(('camayoc_workitem_ingress_coverage_info',
                            {'graph':graph,'coverage':'board-graph-only'},1))
        ok,_=camayoc_metrics.push('camayoc_workitem_ingress',camayoc_metrics.exposition(samples),
                                 grouping={'producer':args.actor})
        if not ok:return 2
    return code


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    source_group=parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument('--db', type=Path, help='separately selected legacy rig only')
    source_group.add_argument('--native-authority-stdin', action='store_true',
                              help='consume the protected selector projection; never a fallback')
    parser.add_argument('--initial-tx', type=int, help='explicitly reviewed first native cursor; never resets existing custody')
    parser.add_argument('--state', required=True, type=Path)
    parser.add_argument('--actor', required=True)
    parser.add_argument('--source', required=True, help='stable authoritative tracker provenance URI')
    parser.add_argument('--publish-status', action='store_true')
    args = parser.parse_args()
    if args.native_authority_stdin:
        return native_main(args)
    if args.initial_tx is not None:
        parser.error('--initial-tx is native-only')
    args.state.parent.mkdir(parents=True, exist_ok=True)
    with args.state.with_suffix('.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({'status': 'BUSY', 'requests': 0}))
            return 0
        started = time.time()
        receipt = {'status': 'UNKNOWN'}
        try:
            state = json.loads(args.state.read_text()) if args.state.exists() else {}
            tracked = [i for i, e in state.get('items', {}).items() if not e.get('final')]
            cache = state.setdefault('deps_cache', {})
            records = collect(args.db, tracked, deps_cache=cache)
            live = {r['id'] for r in records}
            state['deps_cache'] = {i: v for i, v in cache.items() if i in live}
            max_items, policy = batch_policy(args.state.with_suffix('.batch.json'), time.time())
            receipt = tick(records, state, args.state, actor=args.actor,
                           source=args.source, now=started,
                           post=lambda endpoint, body: planes._post(endpoint, body, client='camayoc-ingress'),
                           max_items=max_items)
            if policy:
                receipt['batch_expires_at'] = policy['expires_at']
                receipt['batch_review'] = policy['review']
        except Exception as exc:
            receipt['error'] = type(exc).__name__
        save(args.state.with_suffix('.receipt.json'), {'attempted_at': started, **receipt})
        print(json.dumps(receipt, sort_keys=True))
        code = 2 if receipt['status'] == 'UNKNOWN' else 0
        if args.publish_status:
            samples = [
                ('camayoc_workitem_ingress_timestamp_seconds', {}, started),
                ('camayoc_workitem_ingress_exit_status', {}, code),
                ('camayoc_workitem_ingress_backlog', {}, receipt.get('backlog', -1)),
                ('camayoc_workitem_ingress_oldest_seconds', {}, receipt.get('oldest_seconds', -1)),
                ('camayoc_workitem_ingress_oldest_verified_seconds', {}, receipt.get('oldest_verified_seconds', -1)),
                ('camayoc_workitem_ingress_recheck_utilization', {}, receipt.get('recheck_utilization', -1)),
                ('camayoc_workitem_ingress_unverified_items', {}, receipt.get('unverified_items', -1)),
                ('camayoc_workitem_ingress_indeterminate', {}, receipt.get('indeterminate', -1)),
                ('camayoc_workitem_ingress_pending_oldest_seconds', {}, receipt.get('pending_oldest_seconds', -1)),
                ('camayoc_workitem_ingress_exhausted_retries', {}, receipt.get('exhausted_retries', -1)),
                ('camayoc_workitem_ingress_degraded', {}, int(receipt['status'] == 'DEGRADED')),
            ]
            ok, why = camayoc_metrics.push('camayoc_workitem_ingress',
                camayoc_metrics.exposition(samples), grouping={'producer': args.actor})
            if not ok:
                print(why)
                code = 2
        return code


if __name__ == '__main__':
    raise SystemExit(main())
