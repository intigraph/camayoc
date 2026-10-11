"""Canonical exact-subject Seeds work-item reader (read-only).

The caller supplies ONE deployment-selected protected board. Candidate feeds and
legacy metadata cannot choose authority, override native state, or make a failed
read look like absence. No tracker process, local database, or fallback is used.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass
from urllib.parse import quote, unquote

SCHEMA = 'https://schema.org/'
SEEDS = 'https://seeds.local/ontology/'
ITEM = 'https://seeds.local/item/'
PRINCIPAL = 'https://seeds.local/principal/'
RDF = 'http://www.w3.org/1999/02/22-rdf-syntax-ns#'
RDFS = 'http://www.w3.org/2000/01/rdf-schema#'
QUECHUA = 'https://scbrown.github.io/quechua/ns#'
PREFIXES = {'schema': SCHEMA, 'seeds': SEEDS, 'rdf': RDF, 'rdfs': RDFS, 'quechua': QUECHUA}
STATES = {'open', 'in_progress', 'blocked', 'deferred', 'closed'}
MAX_ROWS = 128
MAX_BYTES = 256 * 1024
PROPERTIES = {RDF+'type', RDFS+'label', SCHEMA+'name', SCHEMA+'identifier',
              SCHEMA+'description', SCHEMA+'keywords', SCHEMA+'dateCreated',
              SCHEMA+'dateModified', SCHEMA+'version', SCHEMA+'agent', SCHEMA+'about',
              SEEDS+'status', SEEDS+'priority', SEEDS+'issueType', QUECHUA+'blockedOn'}


class NativeUnknown(ValueError):
    """Unproved native authority; callers must not consult stale legacy state."""


def iri(value: str) -> str:
    if not isinstance(value, str):
        raise NativeUnknown('IRI is not text')
    prefix, separator, suffix = value.partition(':')
    return PREFIXES[prefix]+suffix if separator and prefix in PREFIXES else value


def lexical(value) -> str:
    if isinstance(value, dict) and set(value) <= {'value', 'datatype', 'language', 'lang'}:
        value = value.get('value')
    if isinstance(value, str):
        return value
    raise NativeUnknown('literal has an unsupported representation')


def instant(value: str) -> dt.datetime:
    try:
        value = re.sub(r'\.(\d+)', lambda m: '.'+(m.group(1)+'000000')[:6], value, count=1)
        parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError('timezone absent')
        return parsed.astimezone(dt.timezone.utc)
    except (TypeError, ValueError) as exc:
        raise NativeUnknown('native timestamp is not an offset instant') from exc


def item_iri(item_id: str) -> str:
    if not isinstance(item_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', item_id):
        raise NativeUnknown('native identifier is not an exact bounded item ID')
    return ITEM+quote(item_id, safe='')


@dataclass(frozen=True)
class Board:
    graph: str
    prefix: str

    def __post_init__(self):
        if (not isinstance(self.graph, str) or not self.graph.startswith(('https://', 'urn:'))
                or any(c in self.graph for c in '<>"{}\\\n\r\t ')
                or not isinstance(self.prefix,str)
                or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', self.prefix)):
            raise NativeUnknown('board graph and prefix must be explicit safe deployment authority')

    def subject(self, item_id: str) -> str:
        if not item_id.startswith(self.prefix+'-'):
            raise NativeUnknown('item is outside the selected board prefix')
        return item_iri(item_id)


@dataclass(frozen=True)
class Record:
    id: str
    action: str
    graph: str
    version: int
    status: str
    owner: str | None
    created: str
    modified: str
    title: str
    description: str
    labels: tuple[str, ...]
    kind: str
    priority: int
    about: tuple[str, ...]
    blocked_on: tuple[str, ...]

    def tracker_record(self) -> dict:
        return {'id':self.id,'title':self.title,'description':self.description,
                'status':self.status,'assignee':self.owner,'created_at':self.created,
                'updated_at':self.modified,'labels':list(self.labels),'issue_type':self.kind,
                'priority':self.priority,'native_iri':self.action,'source_graph':self.graph,
                'source_version':self.version,'native_about':list(self.about),
                'blocked_on':list(self.blocked_on)}


class Reader:
    def __init__(self, post, board: Board):
        self.post = post
        self.board = board
        self.cache = {}  # Only this evaluation/run; no retained stale authority.

    def get(self, item_id: str) -> Record:
        subject = self.board.subject(item_id)
        if subject in self.cache:
            return self.cache[subject]
        props = ', '.join('<'+p+'>' for p in sorted(PROPERTIES))
        query = (f'SELECT ?s ?p ?o WHERE {{ GRAPH <{self.board.graph}> {{ '
                 f'BIND(<{subject}> AS ?s) <{subject}> ?p ?o . FILTER(?p IN ({props})) '
                 f'}} }} LIMIT {MAX_ROWS+1}')
        try:
            response = self.post('/query', {'query':query})
            if not isinstance(response, dict) or response.get('truncated') is not False:
                raise NativeUnknown('native read is partial')
            rows = response.get('rows')
            if (not isinstance(rows, list) or not 0 < len(rows) <= MAX_ROWS
                    or len(json.dumps(response).encode()) > MAX_BYTES):
                raise NativeUnknown('native record missing, malformed or exceeds read budget')
            values = {}
            for row in rows:
                if (not isinstance(row, dict) or iri(row.get('s')) != subject
                        or iri(row.get('p')) not in PROPERTIES or 'o' not in row):
                    raise NativeUnknown('native response is not the exact requested subject')
                values.setdefault(iri(row['p']), []).append(row['o'])
            record = self._record(item_id, subject, values)
        except NativeUnknown:
            raise
        except (OSError, RuntimeError, ValueError, TypeError, KeyError) as exc:
            raise NativeUnknown('native read failed; no legacy fallback') from exc
        self.cache[subject] = record
        return record

    def _record(self, item_id, subject, values):
        def one(prop, *, optional=False):
            found = values.get(prop, [])
            if optional and not found:
                return None
            if len(found) != 1:
                raise NativeUnknown('native field absent or conflicting: '+prop)
            return found[0]
        def text(prop, *, optional=False):
            value = one(prop, optional=optional)
            return lexical(value) if value is not None else None
        def integer(prop):
            raw = one(prop)
            if isinstance(raw, dict): raw = raw.get('value')
            if isinstance(raw, bool) or not re.fullmatch(r'\d+', str(raw)):
                raise NativeUnknown('native numeric field invalid')
            return int(raw)
        if SCHEMA+'Action' not in {iri(t) for t in values.get(RDF+'type', [])}:
            raise NativeUnknown('native record is not directly typed Action')
        if text(SCHEMA+'identifier') != item_id:
            raise NativeUnknown('native stored ID differs from requested ID')
        status = text(SEEDS+'status')
        if status not in STATES:
            raise NativeUnknown('native status is unknown or conflicting')
        modified, created = text(SCHEMA+'dateModified'), text(SCHEMA+'dateCreated')
        if instant(modified) < instant(created):
            raise NativeUnknown('native version dates conflict')
        version, priority = integer(SCHEMA+'version'), integer(SEEDS+'priority')
        if version < 1 or priority > 4:
            raise NativeUnknown('native version or priority outside domain')
        title = text(SCHEMA+'name')
        label = text(RDFS+'label', optional=True)
        if not title or (label is not None and label != title):
            raise NativeUnknown('native title absent or conflicting')
        owner = one(SCHEMA+'agent', optional=True)
        if owner is not None:
            owner = iri(owner)
            if not owner.startswith(PRINCIPAL) or not owner[len(PRINCIPAL):]:
                raise NativeUnknown('native owner is not an exact principal IRI')
            owner = unquote(owner[len(PRINCIPAL):])
        about = tuple(sorted({iri(v) for v in values.get(SCHEMA+'about', [])}))
        if any(not v.startswith(('http://','https://','urn:')) or any(c in v for c in '<>"{}\\\n\r\t ') for v in about):
            raise NativeUnknown('native about target is not an explicit safe IRI')
        blockers = []
        for value in values.get(QUECHUA+'blockedOn', []):
            target = iri(value)
            if not target.startswith(ITEM):
                raise NativeUnknown('native blocker is not an exact item IRI')
            blocker = unquote(target[len(ITEM):])
            if self.board.subject(blocker) != target:
                raise NativeUnknown('native blocker identity is ambiguous')
            blockers.append(blocker)
        return Record(item_id,subject,self.board.graph,version,status,owner,created,modified,
                      title,text(SCHEMA+'description',optional=True) or '',
                      tuple(sorted({lexical(v) for v in values.get(SCHEMA+'keywords',[])})),
                      text(SEEDS+'issueType'),priority,about,tuple(sorted(set(blockers))))


AUTHORITY_FILE = '/etc/camayoc/tracker-authority.json'


def from_authority(post, authority: dict, *, server: str) -> Reader:
    """Consume the protected selector's deployment projection, not a selector.

    The neutral wrapper supplies this directly; the service receives the same
    projection through reviewed IaC. Ambient backend/graph choices are ignored.
    """
    from urllib.parse import urlsplit
    fields = {'format','backend','server','graph','prefix'}
    if (not isinstance(authority,dict) or set(authority) != fields
            or authority['format'] != 'camayoc-tracker-authority-v1'
            or authority['backend'] != 'seeds'):
        raise NativeUnknown('native authority projection missing or is not Seeds')
    try:
        selected = authority['server'].rstrip('/')
        url = urlsplit(selected)
        if (url.scheme not in ('http','https') or not url.hostname or url.username
                or url.password or url.query or url.fragment or selected != server.rstrip('/')):
            raise ValueError('peer mismatch')
    except (AttributeError, TypeError, ValueError) as exc:
        raise NativeUnknown('native authority endpoint differs from selected transport') from exc
    return Reader(post, Board(authority['graph'],authority['prefix']))


def deployed_reader(post) -> Reader:
    """Root-owned projected authority. Missing configuration fails closed.

    There is no invocation/env path override or default board here. Tests and
    other explicit consumers use from_authority instead of weakening this path.
    """
    import os
    import stat
    from pathlib import Path
    import planes
    path=Path(AUTHORITY_FILE)
    try:
        info=path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022 or info.st_size > 4096:
            raise NativeUnknown('deployed authority is not a protected bounded regular file')
        with path.open() as handle:
            profile=json.load(handle)
    except (OSError, ValueError) as exc:
        raise NativeUnknown('deployed native authority is missing or unreadable') from exc
    return from_authority(post,profile,server=planes.SERVER)
