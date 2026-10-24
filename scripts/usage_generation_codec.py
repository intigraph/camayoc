"""Proposal42 canonical accounting envelopes; pure, with no graph/network writes."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import re
from urllib.parse import urlsplit

from ingest_session_usage import BASE, ONTOLOGY

VERSION = 'srm-generation-v1'
PART_BYTES = 65536
PART_RECORDS = 40
MARKER_BYTES = 2048
STAGED_BYTES = 64 * 1024 * 1024
WRITES_HOUR = 60
READ_BYTES = 8 * 1024 * 1024
HEX = re.compile(r'^[0-9a-f]{64}$')
FIELDS = ('version', 'producer', 'harness', 'session', 'horizon', 'source_sha256',
          'previous', 'parts', 'records')
COUNTS = ('input_uncached', 'cache_read_input', 'cache_write_input', 'output')


def canonical(value):
    def valid(v):
        if v is None or type(v) is bool:
            return
        if type(v) is int:
            if abs(v) > 2**53 - 1:
                raise ValueError('unsafe JSON integer')
        elif isinstance(v, str):
            v.encode('utf-8', 'strict')
        elif isinstance(v, list):
            for x in v:
                valid(x)
        elif isinstance(v, dict) and all(isinstance(k, str) and k.isascii() for k in v):
            for x in v.values():
                valid(x)
        else:
            raise ValueError('noncanonical JSON value')
    valid(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def iri(value):
    if not isinstance(value, str) or any(c in value for c in '<>"{}|^`\\\n\r\t '):
        raise ValueError('invalid IRI')
    parsed = urlsplit(value)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        raise ValueError('IRI must be absolute HTTP(S)')
    value.encode('utf-8', 'strict')
    return value


def horizon(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', value):
        raise ValueError('invalid horizon')
    datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ')
    return value


def text(value):
    if not isinstance(value, str) or not value:
        raise ValueError('missing identity/string')
    value.encode('utf-8', 'strict')
    return value


def count(value):
    if type(value) is not int or value < 0:
        raise ValueError('invalid count')
    return value


def record_row(record):
    """Stable tuple, no seed-assignment identity and no float token conversions."""
    harness = record['harness']
    if harness not in ('claude', 'codex'):
        raise ValueError('unknown harness')
    key = [harness, text(record['session']), text(record['id'])]
    counts = [None if record['counts'].get(k) is None else str(count(record['counts'][k])) for k in COUNTS]
    attribution = record['attribution']
    if attribution not in ('attributed', 'no proven focus', 'overlapping focus'):
        raise ValueError('unknown attribution')
    items = [iri(ONTOLOGY + text(record['bead']))] if attribution == 'attributed' else []
    # Provider derives from the parser harness, never an environment default.
    provider = 'anthropic' if harness == 'claude' else 'openai'
    model = None if record.get('model') is None else text(record['model'])
    model_source = None if record.get('model_source') is None else text(record['model_source'])
    return [key, str(count(record['tokens'])), counts, provider, model,
            model_source, text(record['at']), attribution, items, text(record['agent'])]


def record_iri(row):
    return BASE + 'request/' + digest(row[0])


def validate_manifest(raw, *, producer, harness, session, at):
    def pairs(values):
        out = {}
        for k,v in values:
            if k in out:
                raise ValueError('duplicate JSON key')
            out[k] = v
        return out
    m = json.loads(raw, object_pairs_hook=pairs)
    if not isinstance(m, dict) or set(m) != set(FIELDS) or canonical(m) != raw:
        raise ValueError('manifest is not exact canonical schema')
    if (m['version'],m['producer'],m['harness'],m['session'],m['horizon']) != (VERSION,iri(producer),harness,text(session),horizon(at)):
        raise ValueError('manifest identity mismatch')
    if harness not in ('claude','codex') or not isinstance(m['source_sha256'],str) or not HEX.fullmatch(m['source_sha256']):
        raise ValueError('manifest identity/digest invalid')
    if m['previous'] is not None:
        iri(m['previous'])
    if not isinstance(m['parts'],list):
        raise ValueError('invalid parts')
    total = 0
    for i,p in enumerate(m['parts']):
        if not isinstance(p,list) or len(p)!=4 or type(p[0]) is not int or p[0]!=i:
            raise ValueError('duplicate/noncontiguous partition')
        if not isinstance(p[1],str) or not HEX.fullmatch(p[1]) or not 1<=count(p[2])<=PART_RECORDS or not 1<=count(p[3])<=PART_BYTES:
            raise ValueError('part budget/digest invalid')
        total += p[2]
    if count(m['records']) != total:
        raise ValueError('manifest count mismatch')
    return m


def prepare_rows(records):
    """One session source, unique request keys; no implicit cross-harness merge."""
    if not isinstance(records, list):
        raise ValueError('records must be an array')
    rows = sorted((record_row(r) for r in records), key=lambda r:r[0])
    keys = [tuple(r[0]) for r in rows]
    if len(keys) != len(set(keys)):
        raise ValueError('duplicate request identity')
    if len({tuple(r[0][:2]) for r in rows}) > 1 or len({r[-1] for r in rows}) > 1:
        raise ValueError('mixed session/harness/actor source')
    return rows
