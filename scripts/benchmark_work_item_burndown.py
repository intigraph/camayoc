#!/usr/bin/env python3
"""Private native three-trial cost gate; no server, credential or existing DB.

48,800 unrelated canonical subjects plus 6,300 cohort event contributions.
Checks the stored query's complete daily output against an independent oracle.
"""
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import tempfile
import time

import seed_work_items as n
import work_item_burndown as b
import work_item_burndown_metrics as m


def run():
    binary = shutil.which('quipu')
    if not binary:
        raise RuntimeError('native Quipu required; no install or fallback')
    scope = b.Scope(n.Board('https://tracker.example/board', 'test'),
                    'https://private.example', 'worker', 1, '2026-10')
    now = '2026-10-22T03:01:00Z'
    observations = [b.observation(scope, 'baseline:'+s, 'opening', s,
                    100 if s == 'open' else 0, '2026-10-01T00:00:00.000000Z', scope.board.graph)
                    for s in sorted(b.ACTIVE)]
    expected_closed = {}
    for day in range(1, 22):
        at = f'2026-10-{day:02}T01:00:00.000000Z'
        expected_closed[at[:10]] = 100
        for i in range(100):
            for event, amount in [('close', -1), ('reopen', 1)]:
                key = f'{day}:{i}:{event}'
                observations.append(b.observation(scope, key, 'delta', 'open', amount, at, n.ITEM+'test-'+str(i)))
            observations.append(b.observation(scope, f'{day}:{i}:flow', 'closed', '', 1, at, n.ITEM+'test-'+str(i)))
    observations.append(b.observation(scope, 'ready', 'checkpoint', 'ready', 9000,
                                      '2026-10-22T03:00:00.000000Z', scope.board.graph))
    with tempfile.TemporaryDirectory(prefix='workitem-events-native-') as directory:
        root = Path(directory); home = root/'home'; home.mkdir(); db = root/'private.db'
        env = {k: os.environ[k] for k in ('PATH', 'LANG', 'LD_LIBRARY_PATH', 'ORT_DYLIB_PATH') if k in os.environ}
        env['HOME'] = str(home)
        def command(*args):
            return subprocess.check_output([binary, *args, '--db', str(db)], cwd=root, env=env, text=True, timeout=600)
        def ingest(graph, raw, name):
            file = root/name; file.write_bytes(raw)
            command('graph', 'create', graph, '--timestamp', now)
            command('ingest', str(file), '--graph', graph, '--timestamp', now,
                    '--declare-count', str(len(raw.splitlines())), '--declare-sha256', hashlib.sha256(raw).hexdigest(), '--format', 'ttl')
        unrelated = []
        for i in range(48800):
            subject = '<'+n.ITEM+'other-'+str(i)+'>'
            for predicate, value in [(n.RDF+'type', '<'+n.SCHEMA+'Action>'),
                                     (n.SCHEMA+'agent', '<'+n.PRINCIPAL+'owner'+str(i % 40)+'>'),
                                     (n.SEEDS+'priority', str(i % 5)),
                                     (n.SEEDS+'status', '"open"')]:
                unrelated.append(f'{subject} <{predicate}> {value} .')
        ingest(scope.board.graph, ('\n'.join(unrelated)+'\n').encode(), 'unrelated.ttl')
        raw = ('\n'.join(o[2] for o in observations)+'\n').encode()
        ingest(scope.graph, raw, 'events.ttl')
        with (root/'stderr').open('w') as err:
            proc = subprocess.Popen([binary, 'mcp', '--db', str(db)], cwd=root, env=env,
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err, text=True)
            serial = 0
            def send(body):
                proc.stdin.write(json.dumps(body)+'\n'); proc.stdin.flush()
            def receive():
                if not select.select([proc.stdout], [], [], 60)[0]:
                    raise TimeoutError('private native MCP timed out')
                return json.loads(proc.stdout.readline())
            def call(name, args):
                nonlocal serial
                serial += 1
                send(dict(jsonrpc='2.0', id=serial, method='tools/call', params=dict(name=name, arguments=args)))
                result = receive()['result']
                if result.get('isError'):
                    raise RuntimeError('private native refusal: '+str(result))
                return json.loads(result['content'][0]['text'])
            try:
                send(dict(jsonrpc='2.0', id=0, method='initialize', params=dict(protocolVersion='2025-03-26', capabilities={}, clientInfo=dict(name='private-workitem-events-cost', version='1'))))
                assert receive()['id'] == 0
                send(dict(jsonrpc='2.0', method='notifications/initialized'))
                definition = json.loads(m.DEFINITION.read_text())
                call('quipu_queries', definition)
                params = m.parameters(scope, now)
                timings = []
                for _ in range(3):
                    start = time.monotonic()
                    body = call('quipu_ask', dict(name=definition['name'], params=params))
                    timings.append(time.monotonic()-start)
                    samples = m.validate(body, definition, params)
                    active = {labels['status']: value for name, labels, value in samples if name == 'seeds_workitem_events_active'}
                    assert active == {s: 100 if s == 'open' else 0 for s in b.ACTIVE}, active
                    closed = {labels['day']: value for name, labels, value in samples if name == 'seeds_workitem_events_closed_daily'}
                    assert closed == (expected_closed | {'2026-10-22': 0}), closed
                    created = [value for name, _, value in samples if name == 'seeds_workitem_events_created_daily']
                    assert len(created) == 22 and not any(created)
                    daily = {(labels['day'], labels['status']): value for name, labels, value in samples if name == 'seeds_workitem_events_daily'}
                    assert len(daily) == 22*len(b.ACTIVE)
                    assert all(value == (100 if status == 'open' else 0) for (day, status), value in daily.items())
                report = dict(unrelated_subjects=48800, unrelated_owners=40, event_observations=len(observations),
                              aggregate_rows=body['count'], wire_bytes=len(json.dumps(body).encode()), seconds=timings,
                              positive_oracle='PASS', under_four_seconds=all(t < 4 for t in timings),
                              production_requests=0, query_source='exact registered Camayoc stored definition')
                print(json.dumps(report, indent=2), flush=True)
                assert report['under_four_seconds'], 'four-second cost gate FAIL'
            finally:
                proc.terminate(); proc.communicate(timeout=10)


if __name__ == '__main__':
    run()
