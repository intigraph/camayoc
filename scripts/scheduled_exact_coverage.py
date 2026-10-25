#!/usr/bin/env python3
"""Default-OFF one-shot scheduled wrapper for the exact read-only coverage adapter.

No installation, cron mutation, metadata writer or automatic retry. The sealed
private plan is part of the separately reviewed activation packet.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import time
import urllib.parse

import exact_work_item_coverage as adapter

FILES = ('scheduled_exact_coverage.py', 'exact_work_item_coverage.py',
         'backfill_work_items.py', 'planes.py', 'quipu_auth.py', 'seed_work_items.py')
PLAN_KEYS = {'format', 'items', 'control', 'board_graph', 'server', 'source_sha256', 'tag'}
MAX_RECEIPT = 1024 * 1024


class Stop(ValueError):
    """Admission or outcome is unknown; do not retry automatically."""


def digest(data):
    return hashlib.sha256(data).hexdigest()


def private_directory(path):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise Stop('private directory required')


def load_plan(path, expected):
    private_directory(path.parent)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise Stop('private plan required')
        raw = stream.read(16385)
    if len(raw) > 16384 or digest(raw) != expected:
        raise Stop('plan digest differs')
    plan = json.loads(raw)
    if not isinstance(plan, dict) or set(plan) != PLAN_KEYS or plan['format'] != 'camayoc-scheduled-coverage-v1':
        raise Stop('invalid plan')
    origin = urllib.parse.urlsplit(plan['server'])
    if (origin.scheme not in ('http', 'https') or not origin.hostname or origin.username or
            origin.password or origin.query or origin.fragment or origin.path):
        raise Stop('sealed server origin required')
    items = plan['items']
    if (not isinstance(items, list) or len(items) != 2 or len(set(items)) != 2 or
            plan['control'] != items[0]):
        raise Stop('exact two sealed targets and first positive control required')
    for item in items:
        adapter.native.Board(plan['board_graph'], items[0].split('-', 1)[0]).subject(item)
    if not isinstance(plan['tag'], str) or not re.fullmatch(r'[a-z0-9-]{1,48}', plan['tag']):
        raise Stop('invalid exact entry tag')
    hashes = plan['source_sha256']
    if (not isinstance(hashes, dict) or set(hashes) != set(FILES) or
            any(not isinstance(v, str) or not re.fullmatch('[0-9a-f]{64}', v) for v in hashes.values())):
        raise Stop('complete source pin required')
    return plan


def entry(path, sha, plan):
    for value in (sys.executable, str(Path(__file__).resolve()), str(path)):
        if not value.isascii() or not value.isprintable() or '%' in value:
            raise Stop('path cannot be represented as one cron command')
    command = shlex.join([sys.executable, str(Path(__file__).resolve()), '--enable',
                          '--manifest', str(path), '--manifest-sha256', sha])
    return '17 * * * * ' + command + ' # camayoc-exact-coverage:' + plan['tag']


def cleanup(current, exact):
    """Filter the CURRENT locked crontab; never replay an archived preimage."""
    lines = current.splitlines(keepends=True)
    matches = [i for i, line in enumerate(lines) if line.rstrip('\r\n') == exact]
    if len(matches) != 1:
        raise Stop('expected exactly one matching current entry')
    return ''.join(line for i, line in enumerate(lines) if i != matches[0])


def source_receipt(plan):
    root = Path(__file__).resolve().parent
    actual = {}
    for name in FILES:
        p = root / name
        if p.is_symlink() or not p.is_file():
            raise Stop('source identity unavailable')
        actual[name] = digest(p.read_bytes())
    if actual != plan['source_sha256']:
        raise Stop('source hash differs')
    return actual


def checked_output(argv, maximum=MAX_RECEIPT):
    # Local metadata readers only. Never include their raw errors in a receipt.
    result = subprocess.run(argv, capture_output=True, timeout=5, check=False)
    if result.returncode or len(result.stdout) > maximum:
        raise Stop('local scheduler evidence unavailable')
    return result.stdout


def proc_identity(pid):
    root = Path('/proc') / str(pid)
    before = (root / 'stat').read_text()
    fields = before[before.rfind(')') + 2:].split()
    result = {'pid': pid, 'ppid': int(fields[1]), 'start_ticks': int(fields[19]),
              'uid': root.stat().st_uid, 'exe': os.readlink(root / 'exe')}
    if (root / 'stat').read_text().split(') ', 1)[1].split()[19] != str(result['start_ticks']):
        raise Stop('scheduler process changed')
    return result


def verify_scheduler(exact, current, ancestry, records, now):
    """Independent root cron start + live ancestry, not an environment run-id."""
    if current.splitlines().count(exact) != 1:
        raise Stop('exact scheduled entry absent or duplicated')
    # This route supports Debian cron only; unsupported schedulers remain UNKNOWN.
    cron = {p['pid']: p for p in ancestry if p['uid'] == 0 and p['exe'] == '/usr/sbin/cron'}
    if not cron:
        raise Stop('no root cron ancestor')
    command = exact.split(' ', 5)[5]
    user = __import__('pwd').getpwuid(os.getuid()).pw_name
    message = '(' + user + ') CMD (' + command + ')'
    found = []
    for record in records:
        if (record.get('_UID') != '0' or record.get('_EXE') != '/usr/sbin/cron' or
                record.get('_SYSTEMD_UNIT') != 'cron.service' or record.get('MESSAGE') != message):
            continue
        try:
            pid = int(record['_PID'])
            at = int(record['__REALTIME_TIMESTAMP']) / 1000000
            cursor = record['__CURSOR']
        except (KeyError, TypeError, ValueError):
            continue
        if pid in cron and isinstance(cursor, str) and cursor and 0 <= now - at <= 60:
            found.append({'journal_cursor': cursor, 'pid': pid, 'at': at,
                          'start_ticks': cron[pid]['start_ticks'], 'entry_sha256': digest(exact.encode())})
    if len(found) != 1:
        raise Stop('one independent root cron start receipt required')
    return found[0]


def scheduler_receipt(exact):
    current = checked_output(['/usr/bin/crontab', '-l']).decode()
    ancestry = []
    pid = os.getppid()
    for _ in range(12):
        if pid <= 1:
            break
        identity = proc_identity(pid)
        ancestry.append(identity)
        pid = identity['ppid']
    raw = checked_output(['/usr/bin/journalctl', '--unit=cron.service', '--since=-1min',
                          '--output=json', '--no-pager', '--quiet'])
    records = [json.loads(line) for line in raw.splitlines()]
    return verify_scheduler(exact, current, ancestry, records, time.time())


def lock_holders(info):
    """PID and executable basename only; never copy holder arguments/secrets."""
    with open('/proc/locks', 'rb') as stream:
        raw = stream.read(MAX_RECEIPT + 1)
    if len(raw) > MAX_RECEIPT:
        return 'UNKNOWN'
    found = []
    for line in raw.decode().splitlines():
        fields = line.split()
        if len(fields) < 6 or fields[1] != 'FLOCK':
            continue
        try:
            major, minor, inode = fields[5].split(':')
            if (int(major, 16), int(minor, 16), int(inode)) != (os.major(info.st_dev), os.minor(info.st_dev), info.st_ino):
                continue
            pid = int(fields[4])
            name = Path(os.readlink('/proc/' + str(pid) + '/exe')).name
            found.append({'pid': pid, 'executable': name})
        except (ValueError, OSError):
            return 'UNKNOWN'
    return found or 'UNKNOWN'


@contextlib.contextmanager
def locked(paths):
    with contextlib.ExitStack() as stack:
        descriptors = []
        for path in paths:
            fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
            stream = stack.enter_context(os.fdopen(fd, 'r+'))
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                raise Stop('lock identity unavailable')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                # Do not copy potentially secret-bearing holder argv into output.
                raise Stop('lock busy; no child; holders=' + json.dumps(lock_holders(info), sort_keys=True))
            after = path.lstat()
            if (after.st_dev, after.st_ino) != (info.st_dev, info.st_ino):
                raise Stop('lock replaced')
            descriptors.append(fd)
        yield tuple(descriptors)


def persist_new(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(json.dumps(value, sort_keys=True).encode() + b'\n')
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def child_receipt(plan, work, run_id, *, lock_fds=()):
    argv = [sys.executable, str(Path(__file__).resolve().parent / 'exact_work_item_coverage.py'),
            '--enable', '--item', plan['items'][0], '--item', plan['items'][1],
            '--control', plan['control'], '--board-graph', plan['board_graph'], '--run-id', run_id]
    # Bounded reader source is pinned before spawn. Keep all raw child output in
    # private files, never in the scheduler log. Timeout consumes the attempt.
    with os.fdopen(os.open(work / 'stdout', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'wb') as out, \
            os.fdopen(os.open(work / 'stderr', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'wb') as err:
        result = subprocess.run(argv, stdout=out, stderr=err, timeout=73,
                                env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1', QUIPU_SERVER=plan['server']),
                                pass_fds=lock_fds)
    with (work / 'stdout').open('rb') as stream:
        raw = stream.read(MAX_RECEIPT + 1)
    if len(raw) > MAX_RECEIPT:
        raise Stop('child receipt too large')
    receipt = json.loads(raw)
    status = receipt.get('status')
    expected_exit = {'COVERED': 0, 'NOT_COVERED': 1, 'UNKNOWN': 2}.get(status)
    if (expected_exit is None or result.returncode != expected_exit or
            type(receipt.get('writes')) is not int or receipt['writes'] != 0 or
            receipt.get('server') != plan['server']):
        raise Stop('invalid child outcome')
    if (type(receipt.get('requests')) is not int or not 0 <= receipt['requests'] <= 15 or
            not isinstance(receipt.get('seconds'), (int, float)) or not 0 <= receipt['seconds'] <= 70):
        raise Stop('child budget unknown')
    calls = receipt.get('calls')
    if (not isinstance(calls, list) or len(calls) != receipt['requests'] or
            any(not isinstance(c, dict) or not isinstance(c.get('seconds'), (int, float)) or
                not 0 <= c['seconds'] <= 6 or type(c.get('response_bytes')) is not int or
                not 0 <= c['response_bytes'] <= 256 * 1024 for c in calls)):
        raise Stop('per-call budget unknown')
    if status != 'UNKNOWN':
        if (receipt['requests'] != 15 or receipt.get('items') != sorted(plan['items']) or receipt.get('control') != plan['control'] or
                receipt.get('source_sha256') != {k: v for k, v in plan['source_sha256'].items()
                                               if k != 'scheduled_exact_coverage.py'}):
            raise Stop('child scope/source differs')
    if status == 'COVERED' and (receipt.get('covered') != sorted(plan['items']) or receipt.get('missing') != []):
        raise Stop('affirmative coverage inconsistent')
    return receipt


def execute(plan, path, sha, *, scheduler=scheduler_receipt, child=child_receipt):
    work = path.parent
    if load_plan(path, sha) != plan:
        raise Stop('plan changed during admission')
    # Claim the scheduled invocation before admission too: a busy lock or
    # UNKNOWN scheduler/source must not become a retry on the next cron tick.
    # No child is possible until the independent evidence and both locks pass.
    persist_new(work / 'attempt.json', {'state': 'consumed-before-child',
                                      'plan_sha256': sha, 'at': time.time()})
    locks = (Path.home() / '.local/state/camayoc-producer/run.lock',
             Path.home() / '.local/state/camayoc-workitems/seeds-state.lock')
    try:
        schedule = scheduler(entry(path, sha, plan))
        with locked(locks) as descriptors:
            if (Path.home() / '.aegis-hold').exists() or (Path.home() / '.aegis-hold').is_symlink():
                raise Stop('operator hold present')
            hashes = source_receipt(plan)
            persist_new(work / 'admission.json', {'source_sha256': hashes, 'scheduler': schedule,
                                                 'plan_sha256': sha, 'server': plan['server']})
            result = child(plan, work, digest(json.dumps(schedule, sort_keys=True).encode())[:32],
                           lock_fds=descriptors)
            if (source_receipt(plan) != hashes or (Path.home() / '.aegis-hold').exists() or
                    (Path.home() / '.aegis-hold').is_symlink()):
                raise Stop('source or hold changed during child')
    except Exception as error:
        result = {'status': 'UNKNOWN', 'error': type(error).__name__, 'writes': 0,
                  'reason': str(error) if isinstance(error, Stop) else 'outcome unavailable',
                  'attempt_consumed': True}
    persist_new(work / 'result.json', result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--enable', action='store_true')
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--manifest-sha256')
    parser.add_argument('--cleanup-filter', action='store_true')
    args = parser.parse_args(argv)
    if not args.enable and not args.cleanup_filter:
        print(json.dumps({'status': 'OFF', 'requests': 0, 'writes': 0}))
        return 0
    try:
        if not args.manifest or not args.manifest.is_absolute():
            raise Stop('absolute sealed manifest required')
        plan = load_plan(args.manifest, args.manifest_sha256)
        exact = entry(args.manifest, args.manifest_sha256, plan)
        if args.cleanup_filter:
            if args.enable:
                raise Stop('cleanup and execution are separate')
            sys.stdout.buffer.write(cleanup(sys.stdin.buffer.read().decode(), exact).encode())
            return 0
        result = execute(plan, args.manifest, args.manifest_sha256)
        print(json.dumps({'status': result['status'], 'writes': 0, 'attempt_consumed': True}))
        return {'COVERED': 0, 'NOT_COVERED': 1}.get(result['status'], 2)
    except Exception as error:
        # Filter failure emits no partial replacement crontab.
        print(json.dumps({'status': 'STOP', 'error': type(error).__name__,
                          'reason': str(error) if isinstance(error, Stop) else 'admission unavailable'}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
