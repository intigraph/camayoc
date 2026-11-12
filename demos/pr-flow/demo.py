"""Synthetic mapping demo, run from the repository root."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / 'scripts'))
import github_ingress as gi
payload = json.loads(Path('tests/fixtures/github/deliveries/pull_request.opened.json').read_text())['body']
payload['pull_request'].update(mergeable=False, mergeable_state='dirty')
payload['pull_request']['base']['sha'] = 'b' * 40
body = gi.episode_for('pull_request', payload, actor='synthetic-demo')[0]
p = body['nodes'][0]['properties']
print(json.dumps({k:p.get(k,'UNKNOWN') for k in ('githubMergeable','githubMergeableState','githubBaseSha')},sort_keys=True))
