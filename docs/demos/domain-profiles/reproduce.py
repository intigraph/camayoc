"""Record source ownership change and canonical Seeds validation, without a store."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from rdflib import Graph,RDF,SH,URIRef
ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location('seed_profile',ROOT/'scripts/seed_profile.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
base='769a333ec5a654449720d7b1d84d85c0ba49178f'
old=Graph().parse(data=subprocess.check_output(['git','show',base+':shapes/core.shapes.ttl'],cwd=ROOT).decode(),format='turtle')
new=Graph().parse(ROOT/'shapes/core.shapes.ttl')
legacy=URIRef('http://aegis.gastown.local/ontology/WorkItem')
import os
profiles=m.profiles(source=os.environ.get('CAMAYOC_SEEDS_SOURCE'))
with tempfile.TemporaryDirectory() as directory:
 directory=Path(directory);binary=directory/'quipu'
 shutil.copyfile(Path(shutil.which('quipu')).resolve(),binary);binary.chmod(0o700)
 profile=directory/'seeds.ttl';profile.write_text(profiles[0][1])
 fixture=ROOT/'examples/fixtures/examples.ttl'
 good=subprocess.run([str(binary),'validate','--shapes',str(profile),'--data',str(fixture)],capture_output=True,text=True)
 assert good.returncode==0 and good.stdout.startswith('valid'),good.stdout
 bad=Graph().parse(fixture);subject=next(bad.subjects(RDF.type,URIRef('https://schema.org/Action')))
 bad.remove((subject,URIRef('https://schema.org/dateCreated'),None))
 data=directory/'missing-date.ttl';data.write_text(bad.serialize(format='turtle'))
 refused=subprocess.run([str(binary),'validate','--shapes',str(profile),'--data',str(data)],capture_output=True,text=True)
 assert refused.returncode!=0 and 'dateCreated' in refused.stdout,refused.stdout
 print(json.dumps({'base':base,'before_core_workitem_targets':len(list(old.subjects(SH.targetClass,legacy))),
 'after_core_workitem_targets':len(list(new.subjects(SH.targetClass,legacy))),
 'canonical_seeds_fixture_valid':True,'missing_date_refused':True,'store_writes':0,
 'engine_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),'production_activation':False},sort_keys=True))
