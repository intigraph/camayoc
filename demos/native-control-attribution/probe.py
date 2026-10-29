import hashlib,json,pathlib,subprocess,sys,types
source=pathlib.Path(__file__).resolve().parents[3]
sys.path[:0]=[str(source/'scripts'),str(source/'tests')]
import test_seed_work_item_sync as fixture
import seed_work_item_sync as current
baseline=types.ModuleType('baseline_sync')
exec(subprocess.check_output(['git','show','727e025666a3a979c8f20ebe5c0b555bc7089465:scripts/seed_work_item_sync.py'],cwd=source,text=True),baseline.__dict__)
print('Synthetic library fixture: first item verifies; second ASK returns false.')
print('Base: 727e025666a3a979c8f20ebe5c0b555bc7089465')
print('Candidate sync SHA256: '+hashlib.sha256((source/'scripts/seed_work_item_sync.py').read_bytes()).hexdigest())
for label,module in [('BEFORE',baseline),('AFTER',current)]:
 fixture.sync=module
 case=fixture.SerialTicks();case.setUp()
 try:
  original=case.post
  def post(endpoint,body):
   response=original(endpoint,body)
   if body.get('query','').startswith('ASK ') and case.item_of(body['query'])==case.ITEMS[1]:return {'result':False}
   return response
  case.post=post
  receipt=case.tick()
  fields=['status','item','attempted_item','control','requests','writes','verified','backlog']
  print(label+': '+json.dumps({k:receipt[k] for k in fields if k in receipt},sort_keys=True))
  assert (receipt['status'],receipt['requests'],receipt['writes'],receipt['verified'])==('UNKNOWN',8,2,1)
  assert case.j.db.execute('SELECT body FROM pending WHERE id=?',(case.ITEMS[1],)).fetchone()[0]
 finally:case.tearDown()
print('Both retain the failed pending envelope. No live server; original incident remains OPEN.')
