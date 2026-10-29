from pathlib import Path
import json,sys
sys.path.insert(0,str(Path(__file__).resolve().parent/'scripts'))
from ingest_cdevents import episode_for,Quarantined
try:
 body=episode_for(json.loads(Path(sys.argv[1]).read_text()),actor='demo')
except Quarantined as error:
 print('Quarantined: '+str(error));raise SystemExit(2)
node=body['nodes'][0];properties=node['properties']
print('Mapped class: '+node['type'])
print('Outcome: '+properties['deploymentOutcome'])
print('Artifact: '+properties['artifactId'])
print('Preview only: no graph or event was written.')
