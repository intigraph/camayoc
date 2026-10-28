"""Capture-only spool: append without scanning, replay an isolated older prefix.

The typed write FIFO deliberately retains its stronger stop-on-refusal policy.
Quarantine is local evidence, never successful delivery or an automatic retry.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile

from . import offline

MAX_QUARANTINE_BYTES = 16 * 1024 * 1024
MAX_QUARANTINE_ENTRIES = 1024
MAX_QUARANTINE_AGE_DAYS = 7


def _open(path, flags, mode=0o600):
    fd = os.open(path, flags | os.O_NONBLOCK | os.O_NOFOLLOW, mode)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise offline.QueueError('capture buffer is not a regular file')
    return fd


def _size(path):
    try:
        fd = _open(path, os.O_RDONLY)
    except FileNotFoundError:
        return 0
    try:
        return os.fstat(fd).st_size
    finally:
        os.close(fd)


def append(active, prefix, entry):
    """Only stat/tail-byte reads, regardless of pending spool size.

    A newline after a torn append isolates it from the next record. The
    replayer quarantines that fragment; an outage cannot poison all future
    captures. Neither replay nor quarantine holds this mutation lock.
    """
    raw = (json.dumps(entry, sort_keys=True) + '\n').encode()
    if len(raw) > offline.MAX_RECORD_BYTES:
        raise offline.QueueError('capture record exceeds capacity')
    with offline.locked(active):
        size = _size(active)
        separator = b''
        if size:
            fd = _open(active, os.O_RDONLY)
            try:
                os.lseek(fd, -1, os.SEEK_END)
                separator = b'' if os.read(fd, 1) == b'\n' else b'\n'
            finally:
                os.close(fd)
        if size + _size(prefix) + len(separator) + len(raw) > offline.MAX_BYTES:
            raise offline.QueueError('capture buffer is full')
        fd = _open(active, os.O_CREAT | os.O_WRONLY | os.O_APPEND)
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(separator + raw)
            stream.flush()
            os.fsync(stream.fileno())
        offline._sync_dir(active.parent)


def next_head(active, prefix):
    """Rotate in O(1); all scanning/rewrites thereafter use only the prefix.

    The caller owns the capture replay lock. Old-version flushers must have
    stopped before upgrade, as for the previous capture adapter.
    """
    with offline.locked(active):
        if not _size(prefix):
            prefix.unlink(missing_ok=True)
            if not _size(active):
                return b''
            os.replace(active, prefix)
            offline._sync_dir(active.parent)
    fd = _open(prefix, os.O_RDONLY)
    with os.fdopen(fd, 'rb') as stream:
        # Oversized/torn lines are preserved as bounded quarantine fragments.
        return stream.readline(offline.MAX_RECORD_BYTES + 1)


def acknowledge(prefix, raw):
    """No hook writer uses this immutable prefix's mutation lock."""
    with offline.locked(prefix):
        fd = _open(prefix, os.O_RDONLY)
        tmp = None
        try:
            with os.fdopen(fd, 'rb') as source:
                if source.read(len(raw)) != raw:
                    raise offline.QueueError('capture prefix changed; acknowledgment withheld')
                with tempfile.NamedTemporaryFile(dir=prefix.parent, delete=False) as target:
                    tmp = Path(target.name)
                    shutil.copyfileobj(source, target)
                    target.flush()
                    os.fsync(target.fileno())
            os.replace(tmp, prefix)
            offline._sync_dir(prefix.parent)
        finally:
            if tmp is not None:
                tmp.unlink(missing_ok=True)


def quarantine_dir(active):
    return active.with_name('capture-quarantine')


def quarantine(active, raw, reason, at, *, retention=None):
    """Durable archive before acknowledgment; stable IDs make crash retries safe.

    Reason is a fixed category, never a server error containing private data.
    Original bytes, including invalid UTF-8, remain available locally.
    """
    directory = quarantine_dir(active)
    directory.mkdir(mode=0o700, exist_ok=True)
    if directory.is_symlink():
        raise offline.QueueError('capture quarantine is a symlink')
    os.chmod(directory, 0o700)
    key = hashlib.sha256(reason.encode() + b'\0' + raw).hexdigest()
    path = directory / (key + '.json')
    value = dict(id=key, reason=reason, at=at, raw_base64=base64.b64encode(raw).decode())
    encoded = json.dumps(value, sort_keys=True).encode()
    if len(encoded) > MAX_QUARANTINE_BYTES or MAX_QUARANTINE_ENTRIES < 1:
        raise offline.QueueError('capture archive exceeds quarantine capacity')
    with offline.locked(active.with_name('capture-quarantine.jsonl')):
        _prune_locked(active, at, retention=retention)
        if path.exists():
            # Reuse only a verified exact archive after an uncertain local ack.
            existing = read_quarantine(path)
            if existing['reason'] != reason or existing['raw_base64'] != base64.b64encode(raw).decode():
                raise offline.QueueError('quarantine archive differs')
            return key
        _prune_locked(active, at, reserve_bytes=len(encoded), reserve_count=1, retention=retention)
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(dir=directory, prefix='.pending-', mode='wb', delete=False) as stream:
                tmp = Path(stream.name)
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, path)
            offline._sync_dir(directory)
            offline._sync_dir(active.parent)
        finally:
            if tmp is not None:
                tmp.unlink(missing_ok=True)
    return key


def read_quarantine(path):
    fd = _open(path, os.O_RDONLY)
    with os.fdopen(fd, 'rb') as stream:
        raw = stream.read(2 * offline.MAX_RECORD_BYTES + 4096)
    value = json.loads(raw)
    if (not isinstance(value, dict) or value.get('id') != path.stem or
            value.get('reason') not in ('expired', 'malformed', 'destination', 'refused') or
            not isinstance(value.get('at'), str) or not isinstance(value.get('raw_base64'), str)):
        raise offline.QueueError('unreadable quarantine entry')
    base64.b64decode(value['raw_base64'], validate=True)
    return value


def quarantined(active):
    directory = quarantine_dir(active)
    if directory.is_symlink():
        raise offline.QueueError('capture quarantine is a symlink')
    if not directory.exists():
        return []
    # Read-only operator path, never called by capture append/hooks.
    return [read_quarantine(path) for path in sorted(directory.glob('*.json'))]


def clear(active):
    """Explicit local discard; excludes concurrent arrivals through shared lock."""
    with offline.locked(active.with_name('capture-quarantine.jsonl')):
        values = quarantined(active)
        directory = quarantine_dir(active)
        for value in values:
            (directory / (value['id'] + '.json')).unlink()
        if directory.exists():
            offline._sync_dir(directory)
        return len(values)


def _stamp(text):
    value = dt.datetime.fromisoformat(text.replace('Z', '+00:00'))
    if value.tzinfo is None:
        raise offline.QueueError('quarantine timestamp is not timezone-aware')
    return value


def _prune_locked(active, at, *, reserve_bytes=0, reserve_count=0, retention=None):
    """Called only under the quarantine mutation lock, never by hook append.

    Age is measured from archive creation. Capacity evicts oldest archives
    first; metadata and payload disappear together. Read-only inspection never
    prunes. Invalid metadata uses file mtime for retention, never for replay.
    """
    now = _stamp(at)
    directory = quarantine_dir(active)
    if directory.is_symlink():
        raise offline.QueueError('capture quarantine is a symlink')
    rows = []
    for path in directory.glob('*.json'):
        meta = path.lstat()
        if not stat.S_ISREG(meta.st_mode):
            raise offline.QueueError('quarantine archive is not a regular file')
        try:
            stamp = _stamp(read_quarantine(path)['at'])
        except (OSError, ValueError, TypeError, KeyError):
            # Corrupt metadata cannot grant indefinite payload retention. Its
            # content remains UNKNOWN; age falls back to filesystem mtime.
            stamp = dt.datetime.fromtimestamp(meta.st_mtime, dt.timezone.utc)
        rows.append((path, meta.st_size, stamp))
    rows.sort(key=lambda r: (r[2], r[0].name))
    remaining, total, changed = [], 0, False
    counts = retention if retention is not None else dict(expired=0, capacity=0, bytes=0)
    counts.setdefault('orphaned', 0)
    # Under this lock no current writer owns a staged file. Remove remnants of
    # interrupted writes so private payload cannot accumulate outside the cap.
    for path in directory.glob('.pending-*'):
        size = _size(path)
        path.unlink()
        changed = True
        counts['orphaned'] += 1
        counts['bytes'] += size
    for path, size, stamp in rows:
        if stamp < now - dt.timedelta(days=MAX_QUARANTINE_AGE_DAYS):
            path.unlink()
            changed = True
            counts['expired'] += 1
            counts['bytes'] += size
        else:
            remaining.append((path, size))
            total += size
    while remaining and (total + reserve_bytes > MAX_QUARANTINE_BYTES or
                         len(remaining) + reserve_count > MAX_QUARANTINE_ENTRIES):
        path, size = remaining.pop(0)
        path.unlink()
        changed = True
        total -= size
        counts['capacity'] += 1
        counts['bytes'] += size
    if changed:
        offline._sync_dir(directory)
    return counts


def prune(active, at):
    with offline.locked(active.with_name('capture-quarantine.jsonl')):
        return _prune_locked(active, at)


def requeue(active, prefix, at, accept):
    """Local archive -> pending tail, with durable append before archive removal.

    Caller holds capture replay lock. The shared archive lock serializes clear,
    maintenance and requeue. A pending marker avoids duplicate appends after an
    uncertain unlink; if a previous copy already replayed, deterministic capture
    IRIs retain the existing at-least-once replay semantics.
    """
    counts = dict(expired=0, capacity=0, bytes=0)
    moved, skipped, blocked = 0, {}, False
    with offline.locked(active.with_name('capture-quarantine.jsonl')):
        _prune_locked(active, at, retention=counts)
        rows = sorted(quarantined(active), key=lambda r: (_stamp(r['at']), r['id']))
        pending = offline._read(prefix) + offline._read(active)
        markers = {r['_requeued_from']: r for r in pending if isinstance(r.get('_requeued_from'), str)}
        for row in rows:
            entry, reason = accept(base64.b64decode(row['raw_base64']))
            if reason:
                skipped[reason] = skipped.get(reason, 0) + 1
                continue
            entry = dict(entry, _requeued_from=row['id'])
            prior = markers.get(row['id'])
            if prior is not None and prior != entry:
                raise offline.QueueError('pending requeue marker differs')
            if prior is None:
                try:
                    append(active, prefix, entry)
                except offline.QueueError:
                    blocked = True
                    break
                markers[row['id']] = entry
            (quarantine_dir(active) / (row['id'] + '.json')).unlink()
            offline._sync_dir(quarantine_dir(active))
            moved += 1
        remaining = len(quarantined(active))
    return dict(requeued=moved, quarantined=remaining, skipped=skipped,
                capacity_blocked=blocked, retention=counts)
