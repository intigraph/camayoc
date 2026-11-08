#!/usr/bin/env python3
"""Isolated before/after whole-page custody replay, without a live service."""
import importlib.util,json,subprocess,sys,tempfile
from pathlib import Path
from unittest import mock
ROOT=Path(__file__).resolve().parents[3]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'tests')]
import test_seed_work_item_sync as tests
BASE='f6372f6e43e762385b0b573c2ca5432afff544c9'
candidate=tests.sync
with tempfile.TemporaryDirectory() as directory:
 p=Path(directory)/'baseline.py'
 p.write_bytes(subprocess.check_output(['git','-C',str(ROOT),'show',BASE+':scripts/seed_work_item_sync.py']))
 spec=importlib.util.spec_from_file_location('capacity_baseline',p)
 baseline=importlib.util.module_from_spec(spec);spec.loader.exec_module(baseline)
 print('Isolated SQLite custody: 171 pending plus a 100-item four-transaction page; cap 256.')
 for name,implementation in [('before',baseline),('candidate',candidate)]:
  tests.sync=implementation
  case=tests.AdaptiveFeed('test_measured_171_plus_100_falls_back_without_replacing_exact_effect');case.setUp()
  try:
   case.seed(171);calls=[]
   def changes(**args):
    calls.append([args['since'],args['limit']])
    return case.page(args['since']+1,min(14,args['since']+args['limit']),25)
   with mock.patch.object(implementation,'MAX_DELIVERIES',0):result=case.tick(changes)
   print(json.dumps(dict(source=name,cursor=case.j.cursor,pending=result['backlog'],requests=result['requests'],status=result['status'],calls=calls),sort_keys=True))
   assert case.j.cursor==(10 if name=='before' else 13)
   assert result['backlog']<=256 and result['writes']==0
  finally:case.tearDown()
 tests.sync=candidate
 print('PASS: smaller whole pages advance the cursor inside existing custody/read budgets; no live writes.')
