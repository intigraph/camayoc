"""Event-derived scoped burn-down; no board scan or implicit enrollment.

A protected native board supplies bounded whole-transaction ``new_row`` pages.
One pinned, bounded cohort seeds opening balances; later counts use transitions,
not repeated snapshots. An uncertain effect freezes custody until read-back.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import quote, unquote

import seed_work_items as native
import windows

A = 'http://aegis.gastown.local/ontology/'
S = native.SCHEMA
ACTIVE = {'open', 'hooked', 'in_progress', 'blocked', 'deferred'}
PROFILE = 'urn:camayoc:work-item-burndown:'
FORMAT = 'camayoc-work-item-burndown-v1'
MAX_ITEMS = 1000
MAX_RECORDS = 4096
MAX_BYTES = 512 * 1024


class Unknown(ValueError):
    """Incomplete source or uncertain delivery; never a zero balance."""


def integer(value):
    if type(value) is not int or value < 0:
        raise Unknown('invalid nonnegative integer')
    return value


def time(value):
    return native.instant(value).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def iri(value):
    if not isinstance(value, str) or not value.startswith(('https://', 'http://', 'urn:')) or any(c in value for c in '<>"{}\\\n\r\t '):
        raise Unknown('unsafe IRI')
    return '<' + value + '>'


@dataclass(frozen=True)
class Scope:
    board: native.Board
    server: str
    owner: str
    priority: int
    month: str

    def __post_init__(self):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', self.owner):
            raise Unknown('one explicit owner required')
        if type(self.priority) is not int or not 0 <= self.priority <= 4:
            raise Unknown('one explicit priority required')
        iri(self.server)
        windows.window_iri('workitem-burndown/' + self.key, self.month)

    @property
    def key(self):
        value = [self.server, self.board.graph, self.board.prefix, self.owner, self.priority]
        return hashlib.sha256(json.dumps(value).encode()).hexdigest()[:32]

    @property
    def graph(self):
        # The enrollment window remains the stream's custody address; no silent
        # month rollover can lose opening balances or redirect an existing log.
        return windows.window_iri('workitem-burndown/' + self.key, self.month)

    def contains(self, state):
        return bool(state and state['owner'] == self.owner and state['priority'] == self.priority and state['status'] in ACTIVE)


def scalar(value):
    if isinstance(value, dict) and set(value) == {'ref'}:
        return value['ref']
    if isinstance(value, dict):
        return native.lexical(value)
    return value


def state(scope, subject, row):
    if not isinstance(row, dict):
        raise Unknown('full event row missing')
    if not row:
        return None  # A real deletion, never fabricated as a close.
    def one(key, optional=False):
        values = row.get(key, [])
        values = values if isinstance(values, list) else [values]
        if optional and not values:
            return None
        if len(values) != 1:
            raise Unknown('missing or conflicting event field: ' + key)
        return scalar(values[0])
    types = row.get(native.RDF + 'type', [])
    types = types if isinstance(types, list) else [types]
    if S + 'Action' not in [scalar(v) for v in types]:
        raise Unknown('event is not a canonical native Action')
    identifier = one(S + 'identifier')
    if scope.board.subject(identifier) != subject:
        raise Unknown('event identity differs from native subject')
    status = one(native.SEEDS + 'status')
    priority, version = one(native.SEEDS + 'priority'), one(S + 'version')
    if isinstance(priority, str) and re.fullmatch(r'[0-4]', priority):
        priority = int(priority)
    if isinstance(version, str) and re.fullmatch(r'[1-9][0-9]*', version):
        version = int(version)
    if status not in (native.STATES | {'hooked', 'tombstone'}) or type(priority) is not int or not 0 <= priority <= 4 or integer(version) < 1:
        raise Unknown('invalid event state')
    owner = one(S + 'agent', True)
    if owner is not None:
        if not isinstance(owner, str) or not owner.startswith(native.PRINCIPAL):
            raise Unknown('foreign owner')
        raw = owner[len(native.PRINCIPAL):]
        owner = unquote(raw)
        if quote(owner, safe='') != raw or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', owner):
            raise Unknown('ambiguous owner')
    created, modified = time(one(S + 'dateCreated')), time(one(S + 'dateModified'))
    if modified < created:
        raise Unknown('event time regressed')
    return dict(subject=subject, status=status, priority=priority, owner=owner,
                version=version, created=created, modified=modified)


def transitions(scope, page, since):
    """Keep every transaction, deduplicating repeated full rows within one tx."""
    if not isinstance(page, dict) or len(json.dumps(page).encode()) > MAX_BYTES:
        raise Unknown('event page exceeds byte budget')
    end, watermark = integer(page.get('next_tx')), integer(page.get('watermark_tx'))
    records = page.get('records')
    if page.get('capture') != 'new_row' or page.get('truncated', False) is not False or not isinstance(records, list) or len(records) > MAX_RECORDS or not since <= end <= watermark:
        raise Unknown('event page incomplete')
    if not records:
        if end != since:
            raise Unknown('empty page advanced cursor')
        return end, []
    output, seen, last = [], {}, (since, -1)
    for record in records:
        if not isinstance(record, dict):
            raise Unknown('invalid event record')
        tx, seq = integer(record.get('tx')), integer(record.get('sequence'))
        if record.get('graph') != scope.board.graph or not since < tx <= end or (tx, seq) <= last or record.get('op') not in ('assert', 'retract', 'tombstone'):
            raise Unknown('event order or authority differs')
        last = tx, seq
        subject = record.get('entity')
        if not isinstance(subject, str) or not subject.startswith(native.ITEM):
            continue
        identifier = unquote(subject[len(native.ITEM):])
        if '/' in identifier or not identifier.startswith(scope.board.prefix + '-'):
            continue  # Comments and foreign tracker identities are not states.
        current = state(scope, subject, record.get('row'))
        at = time(record.get('timestamp'))
        key = tx, subject
        value = current, at
        if key in seen:
            if seen[key] != value:
                raise Unknown('conflicting full rows within one transaction')
        else:
            seen[key] = value
            output.append((tx, subject, current, at))
    if last[0] != end or len({r['tx'] for r in records}) > 4:
        raise Unknown('whole-transaction boundary unproved')
    return end, output


def observation(scope, key, metric, status, amount, at, about, source_tx=0):
    name = A + 'workitem-burndown-' + hashlib.sha256((scope.key + key).encode()).hexdigest()
    value = json.dumps(dict(metric=metric, status=status, amount=amount, at=at, about=about, source_tx=integer(source_tx)), sort_keys=True, separators=(',', ':'))
    triples = [(native.RDF+'type', iri(A+'Observation')),
               (native.RDFS+'label', json.dumps(name.rsplit('/', 1)[-1])),
               (A+'sourceKind', '"observed"'), (A+'about', iri(about)),
               (A+'observedAt', json.dumps(at)), (A+'observedValue', json.dumps(value)),
               (S+'additionalType', iri(PROFILE+metric)),
               (native.SEEDS+'status', json.dumps(status)),
               (S+'value', str(amount)), (S+'version', str(source_tx))]
    return name, value, '\n'.join(f'{iri(name)} {iri(p)} {v} .' for p, v in triples)


class Journal:
    def __init__(self, path, scope):
        self.scope = scope
        path = Path(path)
        if path.is_symlink():
            raise Unknown('linked custody refused')
        identity = json.dumps(dict(server=scope.server, graph=scope.board.graph, prefix=scope.board.prefix, owner=scope.owner, priority=scope.priority, stream=scope.graph), sort_keys=True)
        if path.exists():
            check = sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)
            try:
                rows = check.execute('SELECT format,scope,cursor FROM authority').fetchall()
                if len(rows) != 1 or rows[0][:2] != (FORMAT, identity):
                    raise Unknown('foreign or unbound custody; no reset or adoption')
            except sqlite3.Error as error:
                raise Unknown('foreign custody format') from error
            finally:
                check.close()
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS authority(format TEXT,scope TEXT,cursor INTEGER)')
        self.db.execute('CREATE TABLE IF NOT EXISTS items(subject TEXT PRIMARY KEY,state TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS pending(body TEXT,expected TEXT,items TEXT,cursor INTEGER)')
        rows = self.db.execute('SELECT format,scope,cursor FROM authority').fetchall()
        identity = json.dumps(dict(server=scope.server, graph=scope.board.graph, prefix=scope.board.prefix, owner=scope.owner, priority=scope.priority, stream=scope.graph), sort_keys=True)
        if rows and (len(rows) != 1 or rows[0][:2] != (FORMAT, identity)):
            self.db.close()
            raise Unknown('foreign custody; no reset or adoption')
        self.identity = identity
        if not rows:
            self.db.execute('INSERT INTO authority VALUES(?,?,NULL)', (FORMAT, identity))

    @property
    def cursor(self):
        row = self.db.execute('SELECT cursor FROM authority').fetchone()
        if not row or row[0] is None:
            raise Unknown('explicit pinned enrollment required')
        return row[0]

    def prepare(self, cursor, updates, observations):
        if self.db.execute('SELECT COUNT(*) FROM pending').fetchone()[0]:
            raise Unknown('uncertain effect pending; read back, never resend')
        body = dict(graph=self.scope.graph, turtle='\n'.join(o[2] for o in observations),
                    actor='camayoc-workitem-burndown', source=self.scope.board.graph+' tx:'+str(cursor))
        if len(json.dumps(body).encode()) > MAX_BYTES:
            raise Unknown('event effect exceeds byte budget')
        expected = {o[0]: o[1] for o in observations}
        self.db.execute('INSERT INTO pending VALUES(?,?,?,?)',
                        (json.dumps(body), json.dumps(expected), json.dumps(updates), cursor))
        return body

    def enroll(self, cursor, records, at):
        integer(cursor); at = time(at)
        if self.db.execute('SELECT cursor FROM authority').fetchone()[0] is not None:
            raise Unknown('custody already enrolled; no reset')
        if not isinstance(records, list) or not 0 < len(records) <= MAX_ITEMS:
            raise Unknown('bounded positive pinned cohort required')
        states = {r['subject']: r for r in records}
        if len(states) != len(records) or any(not self.scope.contains(r) for r in records):
            raise Unknown('baseline differs from selected cohort')
        counts = Counter(r['status'] for r in records)
        observations = [observation(self.scope, 'baseline:'+str(cursor)+status,
                        'opening', status, counts[status], at, self.scope.board.graph, cursor) for status in sorted(ACTIVE)]
        observations.append(observation(self.scope, 'enrollment-checkpoint:'+str(cursor), 'checkpoint', 'catching_up', cursor, at, self.scope.board.graph, cursor))
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.db.execute('UPDATE authority SET cursor=?', (cursor,))
            body = self.prepare(cursor, states, observations)
            self.db.execute('COMMIT')
            return body
        except Exception:
            self.db.execute('ROLLBACK')
            raise

    def consume(self, page, *, observed_at):
        observed_at = time(observed_at)
        end, events = transitions(self.scope, page, self.cursor)
        states = {k: json.loads(v) for k, v in self.db.execute('SELECT subject,state FROM items')}
        updates, observations = {}, []
        for tx, subject, current, at in events:
            previous = updates.get(subject, states.get(subject))
            if previous and current and current['version'] <= previous['version']:
                if current == previous:
                    continue
                raise Unknown('native version regressed or conflicts')
            before, after = self.scope.contains(previous), self.scope.contains(current)
            if before and (not after or previous['status'] != current['status']):
                observations.append(observation(self.scope, f'{tx}:{subject}:leave', 'delta', previous['status'], -1, at, subject, tx))
            if after and (not before or previous['status'] != current['status']):
                observations.append(observation(self.scope, f'{tx}:{subject}:enter', 'delta', current['status'], 1, at, subject, tx))
            if after and current['version'] == 1 and previous is None:
                observations.append(observation(self.scope, f'{tx}:{subject}:create', 'created', '', 1, current['created'], subject, tx))
            if before and current and current['status'] == 'closed':
                observations.append(observation(self.scope, f'{tx}:{subject}:close', 'closed', '', 1, at, subject, tx))
            if before or after or previous is not None:
                updates[subject] = current
        ready = 'ready' if not page['records'] else 'catching_up'
        observations.append(observation(self.scope, 'checkpoint:'+str(end)+':'+observed_at, 'checkpoint', ready, end, observed_at, self.scope.board.graph, end))
        self.db.execute('BEGIN IMMEDIATE')
        try:
            body = self.prepare(end, updates, observations)
            self.db.execute('COMMIT')
            return body
        except Exception:
            self.db.execute('ROLLBACK')
            raise

    def pending(self):
        rows = self.db.execute('SELECT body,expected,items,cursor FROM pending').fetchall()
        if len(rows) > 1:
            raise Unknown('ambiguous pending effect')
        return rows[0] if rows else None

    def acknowledge(self, found):
        """Caller supplies exact immutable observations read from the target graph."""
        pending = self.pending()
        if not pending or found != json.loads(pending[1]):
            raise Unknown('effect readback differs; preserve pending')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            for subject, value in json.loads(pending[2]).items():
                self.db.execute('DELETE FROM items WHERE subject=?', (subject,))
                if value is not None:
                    self.db.execute('INSERT INTO items VALUES(?,?)', (subject, json.dumps(value)))
            self.db.execute('UPDATE authority SET cursor=?', (pending[3],))
            self.db.execute('DELETE FROM pending')
            self.db.execute('COMMIT')
        except Exception:
            self.db.execute('ROLLBACK')
            raise
