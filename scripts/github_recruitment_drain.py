"""Bounded, separate recruitment-inbox consumer; no live adapter or daemon.

Only verified webhook rows enter this inbox. Payloads are wakes, never custody
or approval. A reviewed resolver must return a Candidate ONLY after fresh
eligibility reads; a None result spends no candidate key. The independent
admission callback rechecks authority after the candidate reservation commits.
All callbacks run outside the receiver's SQLite/thread lock.
"""
from __future__ import annotations

import json
import math
import re
import time

from github_recruitment import Attempts, Candidate, attempt

EVENT_HEAD = {'pull_request': ('pull_request', 'head'),
              'pull_request_review': ('pull_request', 'head'),
              'check_run': ('check_run', 'head_sha'),
              'check_suite': ('check_suite', 'head_sha'),
              'workflow_run': ('workflow_run', 'head_sha')}
TERMINAL = ('spawned', 'refused', 'unknown', 'duplicate', 'ignored', 'expired',
            'foreign', 'malformed', 'merge-held', 'stale')
MAX_BATCH = 50
MAX_BODY = 256 * 1024
TWO_DAYS = 2 * 24 * 60 * 60


class _LockedAttempts:
    def __init__(self, spool):
        self.spool = spool
        with spool.lock:
            self.inner = Attempts(spool.conn)

    def reserve(self, *args):
        with self.spool.lock:
            return self.inner.reserve(*args)

    def finish(self, *args, **kwargs):
        with self.spool.lock:
            return self.inner.finish(*args, **kwargs)


def _claim(spool):
    with spool.lock:
        spool.conn.execute('BEGIN IMMEDIATE')
        try:
            row = spool.conn.execute(
                "SELECT delivery,event,body,received_at FROM recruitment_events "
                "WHERE state='pending' ORDER BY received_at,delivery LIMIT 1").fetchone()
            if row is not None:
                spool.conn.execute("UPDATE recruitment_events SET state='attempted' WHERE delivery=? AND state='pending'", (row[0],))
            spool.conn.execute('COMMIT')
            return row
        except BaseException:
            spool.conn.execute('ROLLBACK')
            raise


def _settle(spool, delivery, outcome):
    assert outcome in TERMINAL
    with spool.lock:
        changed = spool.conn.execute(
            "UPDATE recruitment_events SET state=?,reason=? WHERE delivery=? AND state='attempted'",
            (outcome, outcome, delivery)).rowcount
        if changed != 1:
            raise ValueError('recruitment claim absent or already terminal')


def _event_head(event, payload):
    subject, field = EVENT_HEAD[event]
    data = payload.get(subject)
    if not isinstance(data, dict):
        raise ValueError('event subject absent')
    head = data.get(field)
    if field == 'head':
        head = head.get('sha') if isinstance(head, dict) else None
    if not isinstance(head, str) or not re.fullmatch('[0-9a-f]{40}', head):
        raise ValueError('event has no exact head')
    return head


def drain_once(spool, *, repos: frozenset[str], resolve_ready, admit, spawn,
               batch: int = 10, max_age: float = TWO_DAYS, clock=time.time):
    """Consume at most batch verified wakes, independently of graph writes.

    No CLI/transport is exposed by this source primitive. The future installed
    adapters must enforce their own deadlines and native binding/key checks.
    Refused/unknown attempts are never reset; crashes leave claims terminal.
    """
    if (not isinstance(repos, frozenset) or not repos
            or not all(isinstance(x, str) and re.fullmatch(r'intigraph/[A-Za-z0-9_][A-Za-z0-9_.-]*', x) for x in repos)
            or type(batch) is not int or not 1 <= batch <= MAX_BATCH
            or not isinstance(max_age, (int, float)) or isinstance(max_age, bool)
            or not math.isfinite(max_age) or not 0 < max_age <= TWO_DAYS):
        raise ValueError('explicit bounded recruitment policy required')
    ledger = _LockedAttempts(spool)
    counts = {}
    for _ in range(batch):
        row = _claim(spool)
        if row is None:
            break
        delivery, event, raw, received = row
        outcome = 'unknown'
        try:
            now = clock()
            if not math.isfinite(now) or not math.isfinite(received) or received > now or now - received > max_age:
                outcome = 'expired'
            elif event not in EVENT_HEAD:
                outcome = 'ignored'
            elif len(raw) > MAX_BODY:
                outcome = 'malformed'
            else:
                payload = json.loads(raw)
                repo = (payload.get('repository') or {}).get('full_name') if isinstance(payload, dict) else None
                if repo not in repos:
                    outcome = 'foreign'
                else:
                    event_head = _event_head(event, payload)
                    candidate = resolve_ready(event, payload)
                    if candidate is None:
                        outcome = 'ignored'
                    elif not isinstance(candidate, Candidate):
                        outcome = 'malformed'
                    else:
                        candidate.validate()
                        if candidate.repo != repo or candidate.head != event_head:
                            outcome = 'stale'
                        elif candidate.job != 'review':
                            outcome = 'merge-held'
                        elif event.startswith('pull_request') and (payload['pull_request'].get('number') != candidate.pr
                                or type(payload['pull_request'].get('number')) is not int):
                            outcome = 'stale'
                        else:
                            # Refusal text can contain transport output; retain a
                            # fixed label only, never an exception or caller string.
                            def bounded_admit(c):
                                return None if admit(c) is None else 'authority-refused'
                            outcome = attempt(ledger, candidate, delivery=delivery, event_at=received,
                                              admit=bounded_admit, spawn=spawn, clock=clock)
        except Exception:
            # No automatic delivery retry after an indeterminate resolver/call.
            # BaseException (process death) keeps the durable 'attempted' claim.
            outcome = 'unknown'
        _settle(spool, delivery, outcome)
        counts[outcome] = counts.get(outcome, 0) + 1
    return counts


def purge_terminal_bodies(spool, *, now: float, retain_seconds: float = TWO_DAYS, batch: int = 100):
    """Erase only old terminal payload bytes; keep tombstones and attempt keys.

    Pending and interrupted claims retain their evidence. No reset, vacuum,
    candidate-key deletion or automatic replay is a retention operation.
    """
    if (type(batch) is not int or not 1 <= batch <= 1000
            or isinstance(retain_seconds, bool) or not isinstance(retain_seconds, (int, float))
            or not math.isfinite(retain_seconds) or not 0 < retain_seconds <= TWO_DAYS
            or not isinstance(now, (int, float)) or isinstance(now, bool) or not math.isfinite(now)):
        raise ValueError('bounded explicit retention policy required')
    marks = ','.join('?' for _ in TERMINAL)
    with spool.lock:
        return spool.conn.execute(
            "UPDATE recruitment_events SET body=x'' WHERE delivery IN ("
            "SELECT delivery FROM recruitment_events WHERE state IN (" + marks + ") "
            "AND received_at < ? AND length(body)>0 ORDER BY received_at,delivery LIMIT ?)",
            (*TERMINAL, now - retain_seconds, batch)).rowcount
