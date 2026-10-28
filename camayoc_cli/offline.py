"""Durable local FIFO. Only successful heads are acknowledged; no network under
its mutation lock. A separate nonblocking lock permits one ordered replayer.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import stat
import tempfile

MAX_BYTES = 16 * 1024 * 1024
MAX_RECORD_BYTES = 1024 * 1024


class QueueError(ValueError):
    pass


def queue_path(env=None):
    env = os.environ if env is None else env
    root = Path(env.get('XDG_STATE_HOME') or Path.home() / '.local/state')
    return root / 'camayoc' / 'memory-queue.jsonl'


def _read(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    except FileNotFoundError:
        return []
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise QueueError('queue is not a regular file')
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES or (raw and not raw.endswith(b'\n')):
        raise QueueError('queue is over capacity or has an incomplete record')
    try:
        rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    except (ValueError, UnicodeError) as exc:
        raise QueueError('queue contains unreadable records') from exc
    if any(not isinstance(row, dict) for row in rows):
        raise QueueError('queue contains a non-object record')
    return rows


def queue_length(env=None):
    """Missing is zero; unreadable is None, never a healthy empty queue."""
    try:
        return len(_read(queue_path(env)))
    except (OSError, QueueError):
        return None


@contextmanager
def locked(path, *, replay=False):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = path.with_suffix('.replay.lock' if replay else '.lock')
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NONBLOCK | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'a') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise QueueError('lock is not a regular file')
        fcntl.flock(stream, fcntl.LOCK_EX | (fcntl.LOCK_NB if replay else 0))
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def _sync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def enqueue(call, env=None, *, path=None):
    path = queue_path(env) if path is None else Path(path)
    raw = (json.dumps(call, sort_keys=True) + '\n').encode()
    if len(raw) > MAX_RECORD_BYTES:
        raise QueueError('record exceeds local buffer capacity')
    with locked(path):
        rows = _read(path)
        if path.exists() and path.stat().st_size + len(raw) > MAX_BYTES:
            raise QueueError('local buffer is full')
        fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        _sync_dir(path.parent)
        return len(rows) + 1


def head(path):
    with locked(path):
        rows = _read(path)
        return rows[0] if rows else None


def acknowledge(path, expected):
    """Re-read under lock so appends made during a network call survive."""
    with locked(path):
        rows = _read(path)
        if not rows or rows[0] != expected:
            raise QueueError('queue head changed; replay acknowledgment withheld')
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as stream:
                tmp = Path(stream.name)
                for row in rows[1:]:
                    stream.write(json.dumps(row, sort_keys=True) + '\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, path)
            _sync_dir(path.parent)
        finally:
            if tmp is not None:
                tmp.unlink(missing_ok=True)


def capture_length(env=None):
    try:
        return (len(_read(queue_path(env).with_name('capture-spool.inflight.jsonl'))) +
                len(_read(queue_path(env).with_name('capture-spool.jsonl'))))
    except (OSError, QueueError):
        return None


def backlog(env=None):
    from . import capture_spool
    records, captures = queue_length(env), capture_length(env)
    try:
        quarantined = len(capture_spool.quarantined(queue_path(env).with_name('capture-spool.jsonl')))
    except (OSError, ValueError, TypeError, KeyError):
        quarantined = None
    return dict(schema='camayoc-backlog/1', records=records, captures=captures, quarantined_captures=quarantined,
                queued=records + captures if records is not None and captures is not None else None)
