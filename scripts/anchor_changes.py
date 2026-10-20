"""Private/reference anchor evaluator; no network, writes, cursor or scheduler.

# arming: library — a consumer supplies bounded, graph-scoped committed versions.
Pending schema/profile acceptance; deliberately not registered as a live adapter.
Chaski owns durable inbox/cursor acknowledgement, transitions and the outbox.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

MAX_BATCH = 64
HASH = re.compile(r'[0-9a-f]{64}')


class Unknown(ValueError):
    """Missing, ambiguous or over-budget evidence must retain prior state."""


def iri(value):
    if (not isinstance(value, str) or not value or
            any(c.isspace() or c in '<>"{}\\' for c in value) or
            not urlsplit(value).scheme):
        raise Unknown('expected an exact absolute IRI')
    return value


def digest(value):
    if not isinstance(value, str) or not HASH.fullmatch(value):
        raise Unknown('expected lowercase SHA256')
    return value


@dataclass(frozen=True)
class Binding:
    """One directional, relation-specific baseline; not one hash per entity."""
    id: str
    source_graph: str
    source: str
    target_graph: str
    target: str
    expected_hash: str
    recipe: str
    owner: str | None
    direction: str
    epoch: str
    version: int

    def __post_init__(self):
        for value in (self.id, self.source_graph, self.source,
                      self.target_graph, self.target):
            iri(value)
        digest(self.expected_hash)
        if (not isinstance(self.epoch, str) or not self.epoch.strip() or
                len(self.epoch) > 256):
            raise Unknown('missing source-log epoch')
        if not isinstance(self.version, int) or isinstance(self.version, bool) or self.version < 0:
            raise Unknown('invalid baseline version')
        if self.direction not in {'file-to-entity', 'entity-to-file'}:
            raise Unknown('unsupported anchor direction')
        if not isinstance(self.recipe, str) or not self.recipe.strip():
            raise Unknown('missing fingerprint recipe')
        if self.owner is not None and (not isinstance(self.owner, str) or
                                       not self.owner.strip()):
            raise Unknown('invalid owner')


def judge(binding, current):
    """Only an authoritative current version or explicit tombstone can be DUE.

    version is the transaction that CHANGED the fingerprint, never a read time.
    An absent row is UNKNOWN, not a deletion. Recipe changes are UNKNOWN.
    """
    base = {'entity': binding.id, 'owner': binding.owner,
            'verdict': 'UNKNOWN', 'event_id': None, 'evidence': None,
            'target': binding.target, 'target_graph': binding.target_graph,
            'direction': binding.direction}
    if current is None:
        return {**base, 'reason': 'current source version missing'}
    if (current.get('graph'), current.get('entity')) != (
            binding.source_graph, binding.source):
        raise Unknown('source graph/identity mismatch')
    if (not isinstance(current.get('version'), int) or
            isinstance(current['version'], bool) or current['version'] < 0):
        raise Unknown('missing committed source version')
    if current.get('epoch') != binding.epoch or current['version'] < binding.version:
        return {**base, 'reason': 'source epoch differs or version predates baseline'}
    if current.get('recipe') != binding.recipe:
        return {**base, 'reason': 'fingerprint recipe differs'}
    deleted = current.get('deleted', False)
    if not isinstance(deleted, bool):
        raise Unknown('invalid tombstone')
    actual = None if deleted else digest(current.get('hash'))
    evidence = {'binding': binding.id, 'source_graph': binding.source_graph,
                'source': binding.source, 'target_graph': binding.target_graph,
                'target': binding.target, 'direction': binding.direction,
                'old_hash': binding.expected_hash, 'new_hash': actual,
                'recipe': binding.recipe, 'version': current['version'],
                'deleted': deleted, 'epoch': binding.epoch}
    token = hashlib.sha256(json.dumps(evidence, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()
    due = deleted or actual != binding.expected_hash
    return {**base, 'verdict': 'DUE' if due else 'NOT_DUE',
            'event_id': token if due else None, 'evidence': token,
            'basis': evidence}


def route(changes, lookup):
    """Bound-source inverse lookups; no discovery or unrelated population scan.

    lookup(graph, subject) must return a complete bounded binding-ID inventory.
    A truncation/transport error must raise Unknown, never return an empty list.
    Deletion records retain the changed subject, so former bindings still route.
    Binding-edge removal must be routed by the caller using its old_value.
    """
    if not isinstance(changes, list) or len(changes) > MAX_BATCH:
        raise Unknown('change batch exceeds bound')
    subjects = set()
    for change in changes:
        if not isinstance(change, dict):
            raise Unknown('malformed change')
        subjects.add((iri(change.get('graph')), iri(change.get('entity'))))
    found = set()
    for graph, subject in sorted(subjects):
        values = lookup(graph, subject)
        if not isinstance(values, list) or len(values) > MAX_BATCH:
            raise Unknown('inverse binding lookup incomplete/over budget')
        found.update(iri(value) for value in values)
        if len(found) > MAX_BATCH:
            raise Unknown('routed scope exceeds bound')
    return sorted(found)
