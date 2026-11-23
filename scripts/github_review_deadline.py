"""Event-armed review deadlines; no scanner, scheduler installation or live adapter.

The caller authenticates events locally and supplies bounded authoritative reads
and the existing signed spawn transport. A stored deadline is not launch authority.
Connections are private to a caller; callbacks run outside SQLite transactions.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
import time

from github_recruitment import Attempts, Candidate, attempt

REVIEW_DELAY = 2 * 60 * 60
FRESHNESS = 30
SCHEMA = """
CREATE TABLE IF NOT EXISTS review_deadline_events (
 delivery TEXT PRIMARY KEY, state TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS review_deadlines (
 repo TEXT NOT NULL, pr INTEGER NOT NULL, head TEXT NOT NULL,
 item TEXT NOT NULL, due_at REAL NOT NULL, state TEXT NOT NULL, cancel_seq INTEGER NOT NULL,
 PRIMARY KEY(repo,pr,head)
);
CREATE TABLE IF NOT EXISTS review_deadline_cancels (
 seq INTEGER PRIMARY KEY AUTOINCREMENT,
 repo TEXT NOT NULL, pr INTEGER NOT NULL, head TEXT NOT NULL, kind TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS review_cancel_target ON review_deadline_cancels(repo,pr,seq);
"""


@dataclass(frozen=True)
class Wake:
    repo: str
    pr: int
    head: str
    delivery: str
    kind: str

    def validate(self):
        Candidate(self.repo, self.pr, self.head, 'review', 'validation-only').validate()
        if (self.kind not in ('green', 'push', 'review')
                or not isinstance(self.delivery, str)
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', self.delivery)):
            raise ValueError('invalid deadline wake')


@dataclass(frozen=True)
class Readiness:
    candidate: Candidate
    green_since: float
    observed_at: float
    complete_green: bool
    unreviewed: bool


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _ready(value, wake, now, *, due=False):
    if not isinstance(value, Readiness) or not isinstance(value.candidate, Candidate):
        return False
    value.candidate.validate()
    return (value.candidate.job == 'review'
            and (value.candidate.repo, value.candidate.pr, value.candidate.head)
            == (wake.repo, wake.pr, wake.head)
            and value.complete_green is True and value.unreviewed is True
            and _number(value.green_since) and _number(value.observed_at)
            and value.green_since <= value.observed_at <= now
            and now - value.observed_at <= FRESHNESS
            and (not due or now >= value.green_since + REVIEW_DELAY))


class Deadlines:
    def __init__(self, connection, *, repos: frozenset[str]):
        if (not isinstance(repos, frozenset) or not repos
                or not all(isinstance(repo, str) and re.fullmatch(
                    r'intigraph/[A-Za-z0-9_][A-Za-z0-9_.-]*', repo) for repo in repos)
                or connection.in_transaction):
            raise ValueError('private connection and explicit repository policy required')
        self.connection, self.repos = connection, repos
        self.attempts = Attempts(connection)
        connection.executescript(SCHEMA)

    def _sequence(self, wake):
        return self.connection.execute("SELECT COALESCE(MAX(seq),0) FROM review_deadline_cancels WHERE repo=? AND pr=? AND ((kind='push' AND head<>?) OR (kind='review' AND head=?))",
                                       (wake.repo, wake.pr, wake.head, wake.head)).fetchone()[0]

    def _cancelled(self, wake):
        conn, target = self.connection, (wake.repo, wake.pr)
        if conn.execute("SELECT 1 FROM review_deadline_cancels WHERE repo=? AND pr=? AND head=? AND kind='review' LIMIT 1",
                        (*target, wake.head)).fetchone():
            return True
        latest = conn.execute("SELECT head FROM review_deadline_cancels WHERE repo=? AND pr=? AND kind='push' ORDER BY seq DESC LIMIT 1",
                              target).fetchone()
        return latest is not None and latest[0] != wake.head

    def on_event(self, wake, *, verify_event, resolve_ready, clock=time.time):
        """Authenticate one event; arm once or cancel, without scanning PR state.

        verify_event is a LOCAL authenticated-origin check, not a payload boolean.
        resolve_ready makes at most ONE bounded authoritative read for green;
        push/review cancellation makes none. None means eligibility is unknown.
        An armed result's due_at is handed to a separately installed scheduler.
        """
        if not isinstance(wake, Wake):
            raise ValueError('typed wake required')
        wake.validate()
        if wake.repo not in self.repos:
            return {'state': 'foreign'}
        try:
            if verify_event(wake) is not None:
                return {'state': 'refused'}
        except Exception:
            return {'state': 'unknown'}
        conn = self.connection
        if conn.in_transaction:
            raise ValueError('event claim must commit before callbacks')
        if not conn.execute('INSERT OR IGNORE INTO review_deadline_events VALUES (?,?)',
                            (wake.delivery, 'attempted')).rowcount:
            return {'state': 'duplicate'}
        conn.commit()
        result = {'state': 'unknown'}
        try:
            if wake.kind in ('push', 'review'):
                conn.execute('BEGIN IMMEDIATE')
                conn.execute('INSERT INTO review_deadline_cancels(repo,pr,head,kind) VALUES (?,?,?,?)',
                             (wake.repo, wake.pr, wake.head, wake.kind))
                sql = "UPDATE review_deadlines SET state='cancelled' WHERE repo=? AND pr=? AND state='armed'"
                args = (wake.repo, wake.pr)
                sql += ' AND head<>?' if wake.kind == 'push' else ' AND head=?'
                args += (wake.head,)
                conn.execute(sql, args)
                conn.commit()
                result = {'state': 'cancelled'}
            else:
                sequence = self._sequence(wake)
                value = resolve_ready(wake.repo, wake.pr, wake.head)
                now = clock()
                if not _number(now) or not _ready(value, wake, now):
                    result = {'state': 'ignored'}
                else:
                    due = value.green_since + REVIEW_DELAY
                    conn.execute('BEGIN IMMEDIATE')
                    if sequence != self._sequence(wake) or self._cancelled(wake):
                        conn.execute('ROLLBACK')
                        result = {'state': 'cancelled'}
                        changed = False
                    else:
                        changed = conn.execute('INSERT OR IGNORE INTO review_deadlines VALUES (?,?,?,?,?,?,?)',
                            (wake.repo, wake.pr, wake.head, value.candidate.item, due, 'armed', sequence)).rowcount
                    conn.commit()
                    if result['state'] != 'cancelled':
                        result = {'state': 'armed', 'due_at': due} if changed else {'state': 'duplicate'}
        except Exception:
            conn.rollback()
            # No exception text or transport output enters the durable receipt.
            result = {'state': 'unknown'}
        conn.execute('UPDATE review_deadline_events SET state=? WHERE delivery=?',
                     (result['state'], wake.delivery))
        conn.commit()
        return result

    def fire(self, repo, pr, head, *, resolve_ready, admit, spawn, clock=time.time):
        """Fire ONE exact persisted key; no due-list query or periodic scan.

        CAS commits before authority callbacks. Interrupted firing is terminal.
        A launch uses the SAME Attempts key as webhook recruitment. The resolver
        and native custody admission each have one bounded read budget; callers
        enforce their own IO timeouts. No callback is interrupted by this library.
        """
        wake = Wake(repo, pr, head, 'deadline', 'green')
        wake.validate()
        if repo not in self.repos:
            return 'foreign'
        now, conn = clock(), self.connection
        if not _number(now) or conn.in_transaction:
            raise ValueError('valid clock and committed connection required')
        row = conn.execute('SELECT item,due_at,state,cancel_seq FROM review_deadlines WHERE repo=? AND pr=? AND head=?',
                           (repo, pr, head)).fetchone()
        if row is None or row[2] != 'armed':
            return 'duplicate'
        if row[1] > now:
            return 'not-due'
        changed = conn.execute("UPDATE review_deadlines SET state='firing' WHERE repo=? AND pr=? AND head=? AND state='armed'",
                               (repo, pr, head)).rowcount
        conn.commit()
        if not changed:
            return 'duplicate'
        candidate = Candidate(repo, pr, head, 'review', row[0])

        def recheck(c):
            current = resolve_ready(repo, pr, head)
            current_time = clock()
            if (not _number(current_time) or not _ready(current, wake, current_time, due=True)
                    or current.candidate != c or self._sequence(wake) != row[3]):
                return 'readiness-refused'
            if admit(c) is not None or self._sequence(wake) != row[3]:
                return 'authority-refused'
            return None

        state = attempt(self.attempts, candidate, delivery='deadline:' + head,
                        event_at=row[1], admit=recheck, spawn=spawn, clock=clock)
        conn.execute('UPDATE review_deadlines SET state=? WHERE repo=? AND pr=? AND head=? AND state=\'firing\'',
                     (state, repo, pr, head))
        conn.commit()
        return state
