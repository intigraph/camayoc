#!/usr/bin/env bash
# arming: agent-invoked synthetic source demonstration; no network or credentials.
set -euo pipefail
printf 'Target: synthetic private fixture, not an installed service\n'
printf 'Before: native source entrypoint on base135d92a\n'
if git cat-file -e 135d92a95ba349e3c0662767fa624c09d30119f4:scripts/seed_work_item_backfill.py 2>/dev/null; then
    echo 'Unexpected baseline: source already present'; exit 1
else
    echo 'Native entrypoint absent; retired br backfill is the old source'
fi
printf 'After: default OFF command\n'
python3 scripts/seed_work_item_backfill.py --state /tmp/native-backfill-demo/state.sqlite3 --actor fixture
printf 'After: 19 native records, page16, serial delivery4; replay and response-loss controls\n'
PYTHONPATH=tests:scripts python3 - <<'PY'
from test_seed_work_item_backfill import Backfill
import json
fixture=Backfill();fixture.setUp()
try:
    for number in range(1,7):
        print(json.dumps({'tick':number,**fixture.tick()},sort_keys=True))
    assert len(fixture.effects)==len(set(fixture.effects))==19
    print('19 distinct source identities, no duplicate effect on completed sweep')
finally:fixture.tearDown()
fixture=Backfill();fixture.setUp()
try:
    fixture.ids=fixture.ids[:1];fixture.drop=True
    print('Lost writer response:',json.dumps(fixture.tick(),sort_keys=True))
    print('Readback reconciliation:',json.dumps(fixture.tick(),sort_keys=True))
    assert len(fixture.effects)==1
    print('One effect total; the pending body was never resent')
finally:fixture.tearDown()
PY
