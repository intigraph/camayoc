"""Bounded whole-transaction candidate custody, with no replay/reset defaults.

The reviewed caller supplies the initial cursor explicitly. Feed rows are only
candidate IDs; a canonical Reader must obtain current native authority separately.
Cursor and pending IDs commit together, before any projection effect.
"""
from __future__ import annotations
import json
import re
import sqlite3
from urllib.parse import unquote
import seed_work_items as nw

MAX_TRANSACTIONS=4
MAX_RECORDS=4096
MAX_BYTES=512*1024
MAX_PENDING=256

class CustodyUnknown(ValueError):pass

def integer(value):
    if type(value) is not int or value<0:raise CustodyUnknown('invalid transaction boundary')
    return value

def candidates(page,board,since):
    """Validate the complete page before retaining any part or advancing."""
    if not isinstance(page,dict) or len(json.dumps(page).encode())>MAX_BYTES:
        raise CustodyUnknown('feed page exceeds byte budget or is malformed')
    records=page.get('records');end=integer(page.get('next_tx'));watermark=integer(page.get('watermark_tx'))
    if page.get('capture') not in ('new_values','old_and_new_values') or page.get('truncated') or not isinstance(records,list) or len(records)>MAX_RECORDS:
        raise CustodyUnknown('feed page is partial or outside bounds')
    if end<since or watermark<end:raise CustodyUnknown('feed cursor regressed')
    if not records:
        if end!=since:raise CustodyUnknown('empty page cannot advance using global watermark')
        return end,{}
    transactions=set();seen=set();last=(-1,-1);items={}
    for row in records:
        if not isinstance(row,dict) or row.get('graph')!=board.graph:
            raise CustodyUnknown('feed row is not in selected graph')
        tx=integer(row.get('tx'));sequence=integer(row.get('sequence'))
        key=(tx,sequence)
        if not since<tx<=end or key<=last or key in seen or row.get('op') not in ('assert','retract','tombstone'):
            raise CustodyUnknown('feed order or transaction boundary unproved')
        last=key;seen.add(key);transactions.add(tx)
        refs=[row.get('entity')]
        # A Comment may change a parent's searchable/dated metadata. This is
        # exact parent identity, never a text/title search or fuzzy binding.
        if row.get('attribute')==nw.SCHEMA+'parentItem':
            value=row.get('value') or row.get('old_value')
            if isinstance(value,dict):refs.append(value.get('ref'))
        for ref in refs:
            if not isinstance(ref,str) or not ref.startswith(nw.ITEM):continue
            # A Comment's own entity is "<item IRI>/comment/<n>" (item IDs are
            # percent-encoded, so a raw "/" only appears in that sub-path). It
            # changes its parent: map it to that exact parent, and only if the
            # parent re-spells to the same IRI; anything else still refuses
            # (aegis-ksd: one comment otherwise rejected every page).
            comment=re.fullmatch(r'([^/]+)/comment/([0-9]+)',ref[len(nw.ITEM):])
            if comment:
                parent=unquote(comment.group(1))
                if not parent.startswith(board.prefix+'-'):continue
                if board.subject(parent)+'/comment/'+comment.group(2)!=ref:
                    raise CustodyUnknown('comment identity spelling is ambiguous')
                items[parent]=tx
                continue
            item=unquote(ref[len(nw.ITEM):])
            if not item.startswith(board.prefix+'-'):continue
            if board.subject(item)!=ref:raise CustodyUnknown('candidate identity spelling is ambiguous')
            items[item]=tx
    if len(transactions)>MAX_TRANSACTIONS or max(transactions)!=end:
        raise CustodyUnknown('feed page is not a bounded complete transaction boundary')
    return end,items

class Journal:
    def __init__(self,path,board,*,server,initial_tx=None):
        # Custody belongs to the server that received it. Graph and prefix alone
        # let a journal be reopened against a different deployment-selected peer,
        # which then verified the old pending items there and deleted them.
        if not isinstance(server,str) or not server.strip():raise CustodyUnknown('custody needs the receiving server identity')
        self.board=board
        self.db=sqlite3.connect(path,isolation_level=None)
        self.db.execute('PRAGMA synchronous=FULL')
        # Refuse a legacy journal before any CREATE, so the refusal writes nothing.
        legacy=self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='authority'").fetchone()
        if legacy and 'server' not in {c[1] for c in self.db.execute('PRAGMA table_info(authority)')}:
            self.db.close();raise CustodyUnknown('custody has no recorded receiving server; no migration or adoption')
        self.db.execute('CREATE TABLE IF NOT EXISTS authority(graph TEXT,prefix TEXT,cursor INTEGER,server TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS pending(id TEXT PRIMARY KEY,tx INTEGER,body TEXT,effect_tx INTEGER,last_attempt REAL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS verified(id TEXT PRIMARY KEY,version INTEGER)')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row=self.db.execute('SELECT graph,prefix,cursor,server FROM authority').fetchall()
            if not row:
                if initial_tx is None:raise CustodyUnknown('new custody needs an explicitly reviewed initial cursor')
                self.db.execute('INSERT INTO authority VALUES(?,?,?,?)',(board.graph,board.prefix,integer(initial_tx),server))
            elif len(row)!=1 or row[0][:2]!=(board.graph,board.prefix) or row[0][3]!=server:
                raise CustodyUnknown('custody authority mismatch; no migration or reset')
            elif initial_tx is not None and integer(initial_tx)!=row[0][2]:
                raise CustodyUnknown('existing cursor cannot be reset')
            self.db.execute('COMMIT')
        except Exception:
            self.db.execute('ROLLBACK');self.db.close();raise

    @property
    def cursor(self):return self.db.execute('SELECT cursor FROM authority').fetchone()[0]

    def verify_stream_source(self,identity):
        if (not isinstance(identity,dict) or set(identity)!={'store_id','log_epoch'}
                or any(not isinstance(v,str) or not v for v in identity.values())):
            raise CustodyUnknown('explicit stream identity unproved')
        exists=self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='stream_source'").fetchone()
        if not exists:raise CustodyUnknown('stream source is unbound; explicit reviewed transition required')
        rows=self.db.execute('SELECT store_id,log_epoch FROM stream_source').fetchall()
        if rows!=[(identity['store_id'],identity['log_epoch'])]:
            raise CustodyUnknown('stream source changed; no adoption or reset')

    def bind_stream_source(self,identity,expected_cursor):
        """Explicit transition only; caller holds the existing producer lock.

        An absent record can be bound at one exact current cursor. Neither an
        existing binding nor any pending/cursor/effect record may be replaced.
        """
        if (not isinstance(identity,dict) or set(identity)!={'store_id','log_epoch'}
                or any(not isinstance(v,str) or not v for v in identity.values())):
            raise CustodyUnknown('explicit stream identity unproved')
        integer(expected_cursor)
        self.db.execute('BEGIN IMMEDIATE')
        try:
            if self.cursor!=expected_cursor:raise CustodyUnknown('transition cursor changed')
            exists=self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='stream_source'").fetchone()
            if exists:
                self.verify_stream_source(identity)
            else:
                self.db.execute('CREATE TABLE stream_source(store_id TEXT NOT NULL,log_epoch TEXT NOT NULL)')
                self.db.execute('INSERT INTO stream_source VALUES(?,?)',(identity['store_id'],identity['log_epoch']))
            self.db.execute('COMMIT')
        except Exception:self.db.execute('ROLLBACK');raise

    def retain(self,page):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            end,items=candidates(page,self.board,self.cursor)
            for item,tx in items.items():
                self.db.execute('INSERT INTO pending(id,tx) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET tx=MAX(tx,excluded.tx)',(item,tx))
            if self.db.execute('SELECT COUNT(*) FROM pending').fetchone()[0]>MAX_PENDING:
                raise CustodyUnknown('pending custody is full; keep prior cursor')
            self.db.execute('UPDATE authority SET cursor=?',(end,))
            self.db.execute('COMMIT')
        except Exception:self.db.execute('ROLLBACK');raise

    def next(self):
        return self.db.execute('SELECT id,tx,body FROM pending ORDER BY last_attempt,tx,id LIMIT 1').fetchone()

    def mark_attempt(self,item,now):
        import math
        if type(now) not in (int,float) or not math.isfinite(now) or now<0:
            raise CustodyUnknown('attempt clock unproved')
        self.db.execute('UPDATE pending SET last_attempt=? WHERE id=?',(now,item))

    def prepare(self,item,tx,body):
        """Durable exact pending effect before one writer call. Never replace it."""
        serialized=json.dumps(body,sort_keys=True,separators=(',',':'))
        if len(serialized.encode())>nw.MAX_BYTES:raise CustodyUnknown('projection exceeds body budget')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row=self.db.execute('SELECT tx,body FROM pending WHERE id=?',(item,)).fetchone()
            if row!=(tx,None):raise CustodyUnknown('candidate changed or effect already pending; read back, never resend')
            self.db.execute('UPDATE pending SET body=?,effect_tx=? WHERE id=?',(serialized,tx,item))
            self.db.execute('COMMIT')
        except Exception:self.db.execute('ROLLBACK');raise

    def acknowledge(self,item,tx,version):
        """Caller may ack only after independently controlled effect readback."""
        version=integer(version)
        if version<1:raise CustodyUnknown('native version unproved')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            old=self.db.execute('SELECT version FROM verified WHERE id=?',(item,)).fetchone()
            if old and version<old[0]:raise CustodyUnknown('source version regression')
            row=self.db.execute('SELECT tx,effect_tx FROM pending WHERE id=?',(item,)).fetchone()
            if row is None or row[1]!=tx:raise CustodyUnknown('pending receipt does not match custody')
            self.db.execute('INSERT INTO verified VALUES(?,?) ON CONFLICT(id) DO UPDATE SET version=excluded.version',(item,version))
            if row[0]==tx:self.db.execute('DELETE FROM pending WHERE id=?',(item,))
            else:self.db.execute('UPDATE pending SET body=NULL,effect_tx=NULL WHERE id=?',(item,))
            self.db.execute('COMMIT')
        except Exception:self.db.execute('ROLLBACK');raise
