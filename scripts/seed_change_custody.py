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
MAX_RETIRED=8

class CustodyUnknown(ValueError):pass

class CustodyFull(CustodyUnknown):
    """Admitting this transaction would exceed MAX_PENDING; nothing was retained.

    Backpressure, not a fault: the caller keeps the transaction, drains the
    existing pending set and offers the same transaction again.
    """

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
            retired_schema=('CREATE TABLE IF NOT EXISTS retired_effect ('
                            f'slot INTEGER PRIMARY KEY CHECK(slot BETWEEN 1 AND {MAX_RETIRED}),id TEXT UNIQUE NOT NULL,'
                            'body TEXT NOT NULL,tx INTEGER NOT NULL,effect_tx INTEGER NOT NULL,'
                            'write_id TEXT NOT NULL,request_sha256 TEXT NOT NULL,version INTEGER NOT NULL,'
                            'reason TEXT NOT NULL,retired_at REAL NOT NULL,active INTEGER NOT NULL,'
                            'release_reason TEXT)')
            existing=self.db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='retired_effect'").fetchone()
            if existing:
                normalized=re.sub(r'\s+','',existing[0]).lower()
                if 'check(slot=1)' in normalized:
                    # Upgrade only the known one-slot journal, atomically. Every
                    # body, active flag and released history is copied verbatim.
                    self.db.execute('ALTER TABLE retired_effect RENAME TO retired_effect_v1')
                    self.db.execute(retired_schema)
                    self.db.execute('INSERT INTO retired_effect SELECT * FROM retired_effect_v1')
                    self.db.execute('DROP TABLE retired_effect_v1')
                elif f'check(slotbetween1and{MAX_RETIRED})' not in normalized:
                    raise CustodyUnknown('retirement journal schema unknown')
            else:self.db.execute(retired_schema)
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
                raise CustodyFull('pending custody is full; keep prior cursor')
            self.db.execute('UPDATE authority SET cursor=?',(end,))
            self.db.execute('COMMIT')
        except Exception:self.db.execute('ROLLBACK');raise

    def next(self):
        # A newer feed candidate can advance tx while an older exact effect is
        # retained. Read back and acknowledge that effect before the new work.
        row=self.db.execute('SELECT id,CASE WHEN body IS NOT NULL THEN effect_tx ELSE tx END,body '
                            'FROM pending WHERE NOT EXISTS (SELECT 1 FROM retired_effect r '
                            'WHERE r.active=1 AND r.id=pending.id) ORDER BY last_attempt,tx,id LIMIT 1').fetchone()
        if row is not None:integer(row[1])
        return row

    def mark_attempt(self,item,now):
        import math
        if type(now) not in (int,float) or not math.isfinite(now) or now<0:
            raise CustodyUnknown('attempt clock unproved')
        self.db.execute('UPDATE pending SET last_attempt=? WHERE id=?',(now,item))

    def prepare(self,item,tx,body):
        """Durable exact pending effect before one writer call. Never replace it."""
        self.assert_not_retired(item)
        serialized=json.dumps(body,sort_keys=True,separators=(',',':'))
        if len(serialized.encode())>nw.MAX_BYTES:raise CustodyUnknown('projection exceeds body budget')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.assert_not_retired(item)
            row=self.db.execute('SELECT tx,body FROM pending WHERE id=?',(item,)).fetchone()
            if row!=(tx,None):raise CustodyUnknown('candidate changed or effect already pending; read back, never resend')
            self.db.execute('UPDATE pending SET body=?,effect_tx=? WHERE id=?',(serialized,tx,item))
            self.db.execute('COMMIT')
        except Exception:self.db.execute('ROLLBACK');raise

    def release_unconfirmed(self,item,expected_body):
        """Operator release of ONE pending effect whose write was never confirmed.

        Only for an effect the operator has just proved absent on the server (see
        seed_work_item_sync.prove_absent). The row returns to a fresh candidate
        (body/effect_tx NULL, tx kept), so the next normal delivery reads the
        native item, prepares a new exact effect and writes once. Nothing is
        resent here. Refuses a missing, unprepared or changed row, or an effect
        whose version is already verified. Older verified history is retained.
        """
        self.assert_not_retired(item)
        if not isinstance(expected_body,str) or not expected_body:
            raise CustodyUnknown('release needs the exact stored effect')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.assert_not_retired(item)
            row=self.db.execute('SELECT tx,body FROM pending WHERE id=?',(item,)).fetchone()
            if row is None or row[1] is None:raise CustodyUnknown('no stored pending effect to release')
            if row[1]!=expected_body:raise CustodyUnknown('stored effect changed since it was proved absent')
            previous=self.db.execute('SELECT version FROM verified WHERE id=?',(item,)).fetchone()
            if previous:
                envelope=json.loads(expected_body)
                version=envelope.get('version') if isinstance(envelope,dict) else None
                if type(version) is not int or version<=integer(previous[0]):
                    raise CustodyUnknown('effect version already verified or unproved; no automatic repair')
            self.db.execute('UPDATE pending SET body=NULL,effect_tx=NULL WHERE id=? AND body=?',(item,expected_body))
            self.db.execute('COMMIT')
        except Exception:self.db.execute('ROLLBACK');raise
        return integer(row[0])

    def acknowledge(self,item,tx,version):
        """Caller may ack only after independently controlled effect readback."""
        self.assert_not_retired(item)
        version=integer(version)
        if version<1:raise CustodyUnknown('native version unproved')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.assert_not_retired(item)
            old=self.db.execute('SELECT version FROM verified WHERE id=?',(item,)).fetchone()
            if old and version<old[0]:raise CustodyUnknown('source version regression')
            row=self.db.execute('SELECT tx,effect_tx FROM pending WHERE id=?',(item,)).fetchone()
            if row is None or row[1]!=tx:raise CustodyUnknown('pending receipt does not match custody')
            self.db.execute('INSERT INTO verified VALUES(?,?) ON CONFLICT(id) DO UPDATE SET version=excluded.version',(item,version))
            if row[0]==tx:self.db.execute('DELETE FROM pending WHERE id=?',(item,))
            else:self.db.execute('UPDATE pending SET body=NULL,effect_tx=NULL WHERE id=?',(item,))
            self.db.execute('COMMIT')
        except Exception:self.db.execute('ROLLBACK');raise


    def assert_not_retired(self,item):
        if self.db.execute('SELECT 1 FROM retired_effect WHERE id=? AND active=1',(item,)).fetchone():
            raise CustodyUnknown('effect is retired; explicit operator release required')

    def retired_count(self):
        return self.db.execute('SELECT COUNT(*) FROM retired_effect WHERE active=1').fetchone()[0]

    def backlog_count(self):
        # The original pending row is retained, not acknowledged or deleted.
        return self.db.execute('SELECT COUNT(*) FROM pending WHERE NOT EXISTS '
                               '(SELECT 1 FROM retired_effect r WHERE r.active=1 AND r.id=pending.id)').fetchone()[0]

    def retire_effect(self,item,*,write_id,request_sha256,version,effect_tx,reason,now,prove_absent=None):
        """One explicit operator CAS with fresh absence, never UNKNOWN recovery."""
        import hashlib
        import math
        self.board.subject(item)
        if (not all(isinstance(v,str) and re.fullmatch('[0-9a-f]{64}',v) for v in (write_id,request_sha256))
                or type(version) is not int or version<1 or type(effect_tx) is not int or effect_tx<0
                or not isinstance(reason,str) or not reason.strip() or len(reason)>512
                or type(now) not in (int,float) or not math.isfinite(now) or now<0):
            raise CustodyUnknown('exact retirement binding or operator reason unproved')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            if self.db.execute('SELECT COUNT(*) FROM retired_effect').fetchone()[0]>=MAX_RETIRED:
                raise CustodyUnknown('bounded retirement list is full')
            if self.db.execute('SELECT 1 FROM retired_effect WHERE id=?',(item,)).fetchone():
                raise CustodyUnknown('effect already has preserved retirement history')
            row=self.db.execute('SELECT body,tx,effect_tx FROM pending WHERE id=?',(item,)).fetchone()
            if row is None or not row[0] or row[2]!=effect_tx:
                raise CustodyUnknown('exact prepared effect not found')
            previous=self.db.execute('SELECT version FROM verified WHERE id=?',(item,)).fetchone()
            if previous and previous[0]>=version:
                raise CustodyUnknown('effect version already verified; retirement refused')
            envelope=json.loads(row[0])
            request=envelope.get('request')
            digest=hashlib.sha256(json.dumps(request,sort_keys=True).encode()).hexdigest()
            if (not isinstance(request,dict) or envelope.get('write_id')!=write_id
                    or type(envelope.get('version')) is not int or envelope['version']!=version
                    or digest!=request_sha256):
                raise CustodyUnknown('retirement effect binding changed')
            if not callable(prove_absent) or prove_absent(item,envelope) is not True:
                raise CustodyUnknown('fresh exact two-graph effect absence unproved; present effects must complete')
            slot=self.db.execute('SELECT COALESCE(MAX(slot),0)+1 FROM retired_effect').fetchone()[0]
            self.db.execute('INSERT INTO retired_effect VALUES(?,?,?,?,?,?,?,?,?,?,1,NULL)',
                            (slot,item,row[0],row[1],row[2],write_id,request_sha256,version,reason,now))
            self.db.execute('COMMIT')
        except Exception:self.db.execute('ROLLBACK');raise

    def release_retired(self,item,*,write_id,request_sha256,reason):
        """Undo only the scheduling exclusion. The original effect stays frozen.

        No completion proof is invented: normal readback/status reconciliation
        must still succeed before any later candidate can be written.
        """
        if not isinstance(reason,str) or not reason.strip() or len(reason)>512:
            raise CustodyUnknown('operator release reason required')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row=self.db.execute('SELECT body FROM retired_effect WHERE id=? AND write_id=? '
                                'AND request_sha256=? AND active=1',(item,write_id,request_sha256)).fetchone()
            if row is None or self.db.execute('SELECT body FROM pending WHERE id=?',(item,)).fetchone()!=row:
                raise CustodyUnknown('retired effect binding changed')
            self.db.execute('UPDATE retired_effect SET active=0,release_reason=? WHERE id=? AND write_id=? AND request_sha256=? AND active=1',
                            (reason,item,write_id,request_sha256))
            self.db.execute('COMMIT')
        except Exception:self.db.execute('ROLLBACK');raise
