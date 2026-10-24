"""Pure source-contract guards for serial-trickle cost publication (SRM gate b).

The caller must hold the existing producer lock, persist a pending write before
networking, and share this ledger between C and A. These checks perform no I/O.
"""
import math

from usage_generation_codec import (PART_BYTES, PART_RECORDS, MARKER_BYTES,
                                    STAGED_BYTES, WRITES_HOUR, count)

HOUR_SECONDS = 3600
MIN_TICK_SECONDS = 60
A_BYTES_HOUR = 60 * PART_BYTES
TOTAL_BYTES_HOUR = 60 * 1_640_000
MAX_REQUESTS = {'part': 3, 'marker': 2, 'graph': 2, 'graph_label': 2, 'whole': 3}


def timestamp(value):
    if type(value) not in (int,float) or not math.isfinite(value) or value < 0:
        raise ValueError('invalid budget clock')
    return value


def admit(kind, body_bytes, records, requests, now, ledger, staged_bytes=0):
    """Return the exact next ledger; never reset/drop unknown history on error."""
    if kind not in MAX_REQUESTS:
        raise ValueError('unknown cost write class')
    count(body_bytes); count(records); count(requests); count(staged_bytes)
    timestamp(now)
    if requests > MAX_REQUESTS[kind] or requests < 1:
        raise ValueError('per-tick request budget')
    if kind == 'part' and (body_bytes > PART_BYTES or records > PART_RECORDS):
        raise ValueError('part budget')
    if kind == 'marker' and body_bytes > MARKER_BYTES:
        raise ValueError('marker budget')
    if staged_bytes > STAGED_BYTES:
        raise ValueError('aggregate staged-byte budget')
    if not isinstance(ledger,list):
        raise ValueError('unreadable write ledger')
    previous = -1
    for row in ledger:
        if not isinstance(row,dict) or set(row) != {'at','bytes','kind'} or row['kind'] not in MAX_REQUESTS:
            raise ValueError('invalid write receipt')
        stamp = timestamp(row['at']); count(row['bytes'])
        if stamp < previous or stamp > now:
            raise ValueError('clock regression/unsorted receipts')
        previous = stamp
    if ledger and now - ledger[-1]['at'] < MIN_TICK_SECONDS:
        raise ValueError('one C-or-A write per scheduled minute')
    window = [dict(row) for row in ledger if row['at'] > now-HOUR_SECONDS]
    if len(window) >= WRITES_HOUR:
        raise ValueError('cost writes/hour budget')
    if sum(row['bytes'] for row in window)+body_bytes > TOTAL_BYTES_HOUR:
        raise ValueError('C+A body/hour budget')
    if kind != 'whole' and sum(row['bytes'] for row in window if row['kind']!='whole')+body_bytes > A_BYTES_HOUR:
        raise ValueError('A body/hour budget')
    return window + [{'at':now,'bytes':body_bytes,'kind':kind}]


def admit_session(*, c_items, source_digest, previous, staged_bytes, body_bytes):
    """No second in-progress generation, unchanged restage or unbounded staging."""
    count(c_items); count(staged_bytes); count(body_bytes)
    if c_items <= 8:
        raise ValueError('A only serves C-refused oversized sessions')
    if not isinstance(source_digest,str) or len(source_digest)!=64 or any(c not in '0123456789abcdef' for c in source_digest):
        raise ValueError('invalid session source digest')
    if not isinstance(previous,dict) or set(previous)-{'selected','in_progress','legacy'}:
        raise ValueError('unknown generation registry')
    for role in ('selected','in_progress','legacy'):
        if previous.get(role) is not None and not isinstance(previous[role],dict):
            raise ValueError('unknown retained generation')
    selected = previous.get('selected')
    if selected and selected.get('source_digest') == source_digest:
        raise ValueError('unchanged session is not restaged')
    progress = previous.get('in_progress')
    if progress and progress.get('status') not in ('sealed','abandoned'):
        raise ValueError('previous generation is neither sealed nor explicitly abandoned')
    if staged_bytes+body_bytes > STAGED_BYTES:
        raise ValueError('aggregate staged-byte budget')
    # No retirement/delete operation is authorized by this guard.
    return True


def admit_read(*, now, previous, graph, requests=1, response_bytes=0):
    """Consumer-owned persisted attempt fence, one bounded graph read/build/5min."""
    from usage_generation_codec import iri, READ_BYTES
    timestamp(now); iri(graph); count(requests); count(response_bytes)
    if requests != 1 or response_bytes > READ_BYTES:
        raise ValueError('single bounded generation query required')
    if previous is not None:
        if not isinstance(previous,dict) or set(previous)!={'at','graph'}:
            raise ValueError('unknown consumer receipt')
        before=timestamp(previous['at']); iri(previous['graph'])
        if now < before+300:
            raise ValueError('consumer cadence/clock regression')
    return {'at':now,'graph':graph}
