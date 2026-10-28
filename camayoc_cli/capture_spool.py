"""Capture-only spool: append without scanning, replay an isolated older prefix.

The typed write FIFO deliberately retains its stronger stop-on-refusal policy.
Quarantine is local evidence, never successful delivery or an automatic retry.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile

from . import offline


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


def quarantine(active, raw, reason, at):
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
    with offline.locked(active.with_name('capture-quarantine.jsonl')):
        if path.exists():
            # Reuse only a verified exact archive after an uncertain local ack.
            existing = read_quarantine(path)
            if existing['reason'] != reason or existing['raw_base64'] != base64.b64encode(raw).decode():
                raise offline.QueueError('quarantine archive differs')
            return key
        value = dict(id=key, reason=reason, at=at, raw_base64=base64.b64encode(raw).decode())
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(dir=directory, mode='w', delete=False) as stream:
                tmp = Path(stream.name)
                json.dump(value, stream, sort_keys=True)
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
