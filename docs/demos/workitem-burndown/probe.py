#!/usr/bin/env python3
"""Private-fixture library demo; optional argument selects a source checkout."""
from pathlib import Path
import json
import sys
import tempfile

root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[3]
if not (root/'scripts/work_item_burndown.py').is_file():
    print('Event burn-down producer: unavailable in this revision')
    raise SystemExit(0)
sys.path.insert(0, str(root/'scripts'))
import work_item_burndown as b
import seed_work_items as n

at = '2026-10-22T04:00:00Z'
scope = b.Scope(n.Board('https://tracker.example/board', 'test'),
                'https://graph.example', 'worker', 1, '2026-10')
def row(status, version):
    return {n.RDF+'type': {'ref': n.SCHEMA+'Action'},
            n.SCHEMA+'identifier': 'test-a', n.SEEDS+'status': status,
            n.SEEDS+'priority': 1, n.SCHEMA+'agent': {'ref': n.PRINCIPAL+'worker'},
            n.SCHEMA+'version': version, n.SCHEMA+'dateCreated': at,
            n.SCHEMA+'dateModified': at}
with tempfile.TemporaryDirectory(prefix='event-demo-') as directory:
    journal = b.Journal(Path(directory)/'private.sqlite3', scope)
    journal.enroll(10, [b.state(scope, n.ITEM+'test-a', row('open', 1))], at)
    journal.acknowledge(json.loads(journal.pending()[1]))
    print('Opening: open=1; created=0; closed=0 (no invented history)')
    records = [dict(tx=tx, sequence=0, graph=scope.board.graph,
                    entity=n.ITEM+'test-a', row=row(status, version),
                    op='assert', timestamp=at)
               for tx, status, version in [(11, 'closed', 2), (12, 'open', 3)]]
    page = dict(records=records, capture='new_row', next_tx=12,
                watermark_tx=12, truncated=False)
    journal.consume(page, observed_at=at)
    values = [json.loads(v) for v in json.loads(journal.pending()[1]).values()]
    active = 1+sum(v['amount'] for v in values if v['metric']=='delta')
    closed = sum(v['amount'] for v in values if v['metric']=='closed')
    assert (active, closed)==(1, 1)
    assert values[-1]['status']=='catching_up'
    print(f'Close then reopen: open={active}; closed={closed}; coverage=catching_up')
    journal.acknowledge(json.loads(journal.pending()[1]))
    journal.consume(dict(records=[], capture='new_row', next_tx=12,
                         watermark_tx=99, truncated=False), observed_at=at)
    values = [json.loads(v) for v in json.loads(journal.pending()[1]).values()]
    assert values[-1]['status']=='ready'
    print('Empty complete feed page: coverage=ready; unrelated watermark=99')
    journal.db.close()
print('Production requests: 0; deployment acceptance: outstanding')
