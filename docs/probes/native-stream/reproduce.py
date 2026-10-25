import importlib.util,json,sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'scripts'));sys.path.insert(0,str(Path.cwd()/'tests'))
import seed_work_item_sync as current
from test_seed_work_item_sync import Tick
from test_seed_change_custody import page
spec=importlib.util.spec_from_file_location('old_sync',sys.argv[1]);old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
for label,module in [('before',old),('after',current)]:
 fixture=Tick();fixture.setUp();calls=[]
 try:
  def changes(**args):calls.append('GET /changes');return page()
  if label=='after':fixture.j.retain(page())
  receipt=module.tick(fixture.reader,fixture.j,fixture.post,changes=changes if label=='before' else None,plane='urn:private:knowledge',actor='fixture')
  shown={k:receipt[k] for k in ['status','verified','requests','writes','cursor']};shown['feed_polls']=len(calls)
  print(label+': '+json.dumps(shown,sort_keys=True));assert receipt['status']=='OK' and receipt['verified']==1 and receipt['writes']==1
  assert len(calls)==(1 if label=='before' else 0)
 finally:fixture.tearDown()
