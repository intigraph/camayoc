"""Bounded native ingress transport and one-query metric collection.

No automatic enrollment, cursor reset, graph selection or effect retry. The
caller supplies deployment-selected authority and a reviewed cohort.
"""
from __future__ import annotations
import json
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import planes
import seed_work_items as n
import work_item_burndown as b

CLIENT = 'camayoc-workitem-burndown'
READBACK_LIMIT = 129


def expand(value):
    value = b.scalar(value)
    if isinstance(value, str):
        for prefix in ('a:', 'aegis:'):
            if value.startswith(prefix):
                return b.A+value[len(prefix):]
        return n.iri(value)
    return value


class Transport:
    def __init__(self, server, *, client=CLIENT, opener=urlopen):
        self.server, self.client, self.opener = server.rstrip('/'), client, opener
        self.calls = []

    def __call__(self, path, body=None, *, method='POST'):
        # Resolve before HTTP; a missing credential is not an unauthenticated
        # production probe. It never enters argv, logs, or error text.
        token = planes.auth_token()
        if not token:
            raise b.Unknown('nonempty Quipu credential required before HTTP')
        headers = {'Content-Type': 'application/json', 'X-Quipu-Client': self.client,
                   'Authorization': 'Bearer '+token, **planes.provenance_headers(self.client)}
        data = None if method == 'GET' else json.dumps(body).encode()
        req = Request(self.server+path, data=data, headers=headers, method=method)
        started = time.monotonic()
        try:
            with self.opener(req, timeout=4) as response:
                raw = response.read(b.MAX_BYTES+1)
                if len(raw) > b.MAX_BYTES:
                    raise b.Unknown('transport byte budget exceeded')
                return json.loads(raw)
        finally:
            self.calls.append(dict(route=path.split('?')[0], method=method,
                                   seconds=time.monotonic()-started))


def opening_query(scope):
    # Exact assignee + priority indexed cohort only; no whole-board fallback.
    owner = n.PRINCIPAL+scope.owner
    return (f'SELECT ?s ?identifier ?status ?priority ?owner ?created ?modified ?version WHERE {{ GRAPH {b.iri(scope.board.graph)} {{ '
            f'?s a {b.iri(n.SCHEMA+"Action")} ; {b.iri(n.SCHEMA+"agent")} {b.iri(owner)} ; '
            f'{b.iri(n.SEEDS+"priority")} {scope.priority} ; '
            f'{b.iri(n.SCHEMA+"identifier")} ?identifier ; {b.iri(n.SEEDS+"status")} ?status ; '
            f'{b.iri(n.SEEDS+"priority")} ?priority ; {b.iri(n.SCHEMA+"agent")} ?owner ; '
            f'{b.iri(n.SCHEMA+"dateCreated")} ?created ; {b.iri(n.SCHEMA+"dateModified")} ?modified ; '
            f'{b.iri(n.SCHEMA+"version")} ?version . '
            'FILTER(?status IN ("open", "hooked", "in_progress", "blocked", "deferred")) '
            f'}} }} LIMIT {b.MAX_ITEMS+1}')


def opening_records(scope, response):
    if not isinstance(response, dict) or response.get('truncated', False) is not False:
        raise b.Unknown('opening cohort is partial')
    rows = response.get('rows')
    if not isinstance(rows, list) or not 0 < len(rows) <= b.MAX_ITEMS or type(response.get('count')) is not int or response['count'] != len(rows):
        raise b.Unknown('positive bounded opening cohort required')
    records = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'s', 'identifier', 'status', 'priority', 'owner', 'created', 'modified', 'version'}:
            raise b.Unknown('opening fields incomplete')
        fields = {n.RDF+'type': {'ref': n.SCHEMA+'Action'},
                  n.SCHEMA+'identifier': row['identifier'], n.SEEDS+'status': row['status'],
                  n.SEEDS+'priority': row['priority'], n.SCHEMA+'agent': {'ref': expand(row['owner'])},
                  n.SCHEMA+'dateCreated': row['created'], n.SCHEMA+'dateModified': row['modified'],
                  n.SCHEMA+'version': row['version']}
        value = b.state(scope, expand(row['s']), fields)
        if not scope.contains(value):
            raise b.Unknown('opening query returned another cohort')
        records.append(value)
    return records


def readback(post, journal):
    pending = journal.pending()
    if pending is None:
        raise b.Unknown('no pending effect to verify')
    expected = json.loads(pending[1])
    if not 0 < len(expected) < READBACK_LIMIT:
        raise b.Unknown('effect exceeds bounded readback')
    subjects = ' '.join(b.iri(subject) for subject in expected)
    query = (f'SELECT ?s ?value ?at ?kind ?status ?amount ?about WHERE {{ GRAPH {b.iri(journal.scope.graph)} {{ '
             f'VALUES ?s {{ {subjects} }} ?s a {b.iri(b.A+"Observation")} ; '
             f'{b.iri(b.A+"sourceKind")} "observed" ; '
             f'{b.iri(b.A+"observedValue")} ?value ; {b.iri(b.A+"observedAt")} ?at ; '
             f'{b.iri(n.SCHEMA+"additionalType")} ?kind ; {b.iri(n.SEEDS+"status")} ?status ; '
             f'{b.iri(n.SCHEMA+"value")} ?amount ; {b.iri(b.A+"about")} ?about . }} }} LIMIT {READBACK_LIMIT}')
    response = post('/query', dict(query=query))
    rows = response.get('rows') if isinstance(response, dict) else None
    if not isinstance(rows, list) or response.get('truncated', False) is not False or len(rows) != len(expected) or response.get('count') != len(rows):
        raise b.Unknown('effect readback coverage differs')
    found = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'s', 'value', 'at', 'kind', 'status', 'amount', 'about'}:
            raise b.Unknown('effect readback malformed')
        subject = expand(row['s'])
        if subject not in expected or subject in found or row['value'] != expected[subject]:
            raise b.Unknown('effect values differ')
        value = json.loads(expected[subject])
        if (row['at'] != value['at'] or row['status'] != value['status'] or
                expand(row['kind']) != b.PROFILE+value['metric'] or
                row['amount'] != value['amount'] or expand(row['about']) != value['about']):
            raise b.Unknown('effect triples disagree with observed value')
        found[subject] = row['value']
    journal.acknowledge(found)


def send_once(post, journal, body):
    if len(json.loads(journal.pending()[1])) >= READBACK_LIMIT:
        raise b.Unknown('effect exceeds bounded readback; preserve custody')
    response = post('/knot', body)
    if not isinstance(response, dict) or response.get('conforms') is not True or type(response.get('tx_id')) is not int or response['tx_id'] < 0:
        raise b.Unknown('write outcome unknown; preserve pending, no resend')
    readback(post, journal)


def enroll(post, journal, *, cursor, observed_at):
    # Caller must first register+label the explicit operational window using
    # windows.ensure_window, and supply a reviewed committed source tx.
    response = post('/query', dict(query=opening_query(journal.scope), tx=b.integer(cursor)))
    records = opening_records(journal.scope, response)
    body = journal.enroll(cursor, records, observed_at)
    send_once(post, journal, body)
    return dict(status='enrolled', items=len(records), cursor=journal.cursor)


def tick(post, journal, *, observed_at):
    """At most one feed, one effect, one verification; pending = one read only."""
    if journal.pending():
        readback(post, journal)
        return dict(status='reconciled', cursor=journal.cursor)
    params = dict(since=journal.cursor, limit=4, capture='new_row', graph=journal.scope.board.graph)
    page = post('/changes?'+urlencode(params), method='GET')
    body = journal.consume(page, observed_at=observed_at)
    send_once(post, journal, body)
    return dict(status='ready' if not page['records'] else 'catching_up', cursor=journal.cursor)
