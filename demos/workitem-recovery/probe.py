import copy,datetime,hashlib,importlib.util,io,json,pathlib,sys,urllib.error
source=pathlib.Path(sys.argv[1]);spec=importlib.util.spec_from_file_location('probe_tests',source/'tests/test_sync_work_items.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
x=m.Delivery('test_unchanged_success_needs_readback_not_count');x.setUp()
start=100000;clock=start;writes=[];present=False
def post(endpoint,body):
 global present
 x.calls.append((endpoint,copy.deepcopy(body)))
 if endpoint=='/episode':
  writes.append((clock,copy.deepcopy(body)))
  assert json.loads(x.path.read_text())['items']['proj-a']['pending']['body']==body
  if len(writes)==1:raise TimeoutError('lost')
  if clock<start+2400:
   try:raise urllib.error.HTTPError('http://fixture.invalid/episode',503,'Unavailable',{},io.BytesIO())
   except urllib.error.HTTPError as e:raise m.sync.planes.PlaneError('HTTP503') from e
  present=True;return {'outcome':'created','count':24}
 return {'rows':[{'s':'control'}]} if 'LIMIT 1' in body['query'] else {'rows':[{'t':'WorkItem','o':'Observation'}] if present else []}
try:
 for clock in [start,start+60,start+120,start+480,start+540,start+600,start+2280,*range(start+2400,start+14401,60)]:
  r=m.sync.tick([m.RECORD],x.state,x.path,actor='tracker',source='br:fixture',now=clock,post=post)
  assert r['requests']<=3 and r['writes']<=1
  if r['verified']:break
 original=writes[0][1];assert all(body==original for _,body in writes)
 positive=m.Delivery('test_unchanged_success_needs_readback_not_count');positive.setUp()
 try:pr=positive.tick();assert pr['verified']==1
 finally:positive.doCleanups()
 result={'time_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'source_kind':'selected source checkout','source_sha256':hashlib.sha256((source/'scripts/sync_work_items.py').read_bytes()).hexdigest(),'write_offsets':[t-start for t,_ in writes],'final_offset':clock-start,'verified':r['verified'],'status':r['status'],'retained_attempts':x.state['items']['proj-a'].get('pending',{}).get('attempts'),'unchanged_bodies':True,'positive_verified':pr['verified']}
 print(json.dumps(result,indent=2))
finally:x.doCleanups()
