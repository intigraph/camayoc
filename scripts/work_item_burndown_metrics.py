"""Validate one stored aggregate; expose only covered, event-derived balances."""
from __future__ import annotations
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import time

import work_item_burndown as b

DEFINITION = Path(__file__).resolve().parents[1] / 'queries/camayoc_work_item_burndown.json'
MAX_ROWS = 512
MAX_BYTES = 256 * 1024


def parameters(scope, now):
    now = b.time(now)
    end = datetime.fromisoformat(now.replace('Z', '+00:00'))
    start = (end-timedelta(days=30)).replace(hour=0, minute=0, second=0, microsecond=0)
    return dict(graph=scope.graph, start=start.isoformat(timespec='microseconds').replace('+00:00', 'Z'), end=now)


def rendered(definition, params):
    b.iri(params['graph'])
    for key in ('start', 'end'):
        if b.time(params[key]) != params[key]:
            raise b.Unknown('noncanonical UTC query boundary')
    result = definition['template']
    for key, value in params.items():
        result = result.replace('{'+key+'}', value)
    return result


def numeric(value):
    if isinstance(value, dict):
        value = value.get('value')
    if isinstance(value, str):
        if not value or (value.lstrip('-').isdigit() is False):
            raise b.Unknown('aggregate is not an integer')
        value = int(value)
    if type(value) is not int or abs(value) > 10**12:
        raise b.Unknown('aggregate outside integer budget')
    return value


def validate(body, definition, params, *, max_age=900):
    if not isinstance(body, dict) or len(json.dumps(body).encode()) > MAX_BYTES or body.get('truncated', False) is not False:
        raise b.Unknown('partial or oversized aggregate')
    rows = body.get('rows')
    if not isinstance(rows, list) or len(rows) >= MAX_ROWS or body.get('count') != len(rows):
        raise b.Unknown('aggregate row coverage unproved')
    if body.get('query') != definition['name'] or body.get('sparql') != rendered(definition, params):
        raise b.Unknown('stored query identity differs')
    opening, checkpoint, daily, keys = {}, [], defaultdict(dict), set()
    at = None
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'metric', 'status', 'day', 'value'}:
            raise b.Unknown('malformed aggregate row')
        metric, status, day = (row[k] for k in ('metric', 'status', 'day'))
        value = numeric(row['value'])
        key = metric, status, day
        if key in keys:
            raise b.Unknown('duplicate aggregate group')
        keys.add(key)
        if metric == 'checkpoint':
            checkpoint.append((status, b.time(day), value))
        elif metric == 'opening':
            if status not in b.ACTIVE or status in opening or value < 0:
                raise b.Unknown('invalid opening balance')
            date = b.time(day)
            if at is not None and at != date:
                raise b.Unknown('ambiguous enrollment boundary')
            at = date
            opening[status] = value
        elif metric in ('delta', 'created', 'closed'):
            if (metric == 'delta' and status not in b.ACTIVE) or (metric != 'delta' and (status != '' or value < 0)):
                raise b.Unknown('invalid event contribution')
            if day != 'opening':
                try:
                    if datetime.strptime(day, '%Y-%m-%d').date().isoformat() != day:
                        raise ValueError()
                except (ValueError, TypeError):
                    raise b.Unknown('invalid event day') from None
                if not params['start'][:10] <= day <= params['end'][:10]:
                    raise b.Unknown('event outside query window')
            elif metric != 'delta':
                raise b.Unknown('flow before coverage window')
            daily[day][(metric, status)] = value
        else:
            raise b.Unknown('unknown aggregate metric')
    if set(opening) != b.ACTIVE or not sum(opening.values()) or len(checkpoint) != 1:
        raise b.Unknown('positive complete enrollment/checkpoint missing')
    state, fresh, cursor = checkpoint[0]
    age = (datetime.fromisoformat(params['end'].replace('Z', '+00:00')) - datetime.fromisoformat(fresh.replace('Z', '+00:00'))).total_seconds()
    if state != 'ready' or not 0 <= age <= max_age or cursor < 0 or at > fresh:
        raise b.Unknown('event coverage catching up, stale or regressed')
    balances = dict(opening)
    for (metric, status), amount in daily.pop('opening', {}).items():
        balances[status] += amount
    first = max(at[:10], params['start'][:10])
    day = datetime.strptime(first, '%Y-%m-%d').date()
    last = datetime.strptime(params['end'][:10], '%Y-%m-%d').date()
    output = []
    while day <= last:
        name = day.isoformat()
        values = daily.get(name, {})
        for status in sorted(b.ACTIVE):
            balances[status] += values.get(('delta', status), 0)
            if balances[status] < 0:
                raise b.Unknown('negative event-derived balance')
            output.append(('seeds_workitem_events_daily', dict(day=name, status=status), balances[status]))
        for kind in ('created', 'closed'):
            output.append((f'seeds_workitem_events_{kind}_daily', dict(day=name), values.get((kind, ''), 0)))
        day += timedelta(days=1)
    if not output:
        raise b.Unknown('coverage starts after requested instant')
    for status, amount in sorted(balances.items()):
        output.append(('seeds_workitem_events_active', dict(status=status), amount))
    output += [('seeds_workitem_events_coverage_start_timestamp_seconds', {}, datetime.fromisoformat(at.replace('Z', '+00:00')).timestamp()),
               ('seeds_workitem_events_ingress_timestamp_seconds', {}, datetime.fromisoformat(fresh.replace('Z', '+00:00')).timestamp())]
    return output


def collect(post, scope, now, *, clock=time.monotonic):
    """Exactly ONE /ask; its SPARQL evaluation is not zero store work."""
    definition = json.loads(DEFINITION.read_text())
    params = parameters(scope, now)
    start = clock()
    body = post('/ask', dict(name=definition['name'], params=params))
    seconds = clock()-start
    if seconds >= 4:
        raise b.Unknown('stored aggregate exceeded four-second budget')
    samples = validate(body, definition, params)
    labels = dict(assignee=scope.owner, priority=str(scope.priority))
    samples = [(name, labels | dims, value) for name, dims, value in samples]
    samples.append(('seeds_workitem_events_query_duration_seconds', labels, seconds))
    return samples
