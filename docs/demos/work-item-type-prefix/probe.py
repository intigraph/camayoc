"""Offline source demonstration: no credentials, network or producer execution."""
import importlib.util
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts'))
BASE = '769a333ec5a654449720d7b1d84d85c0ba49178f'
SOURCE = 'scripts/seed_work_item_sync.py'


def observed(path, wrong_type=False):
    spec = importlib.util.spec_from_file_location('prefix_demo', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    envelope = {'request': {'graph': 'urn:demo:records'}, 'description': 'Current description'}

    def post(endpoint, body):
        assert endpoint == '/query'
        query = body['query']
        if 'comment>' in query:
            return {'rows': [{'v': envelope['description']}], 'truncated': False}
        value = module.projection.A + ('OtherType' if wrong_type else 'WorkItem')
        if 'STR(' not in query:
            value = 'a:' + value.rsplit('/', 1)[-1]
        return {'rows': [{'t': value}], 'truncated': False}

    try:
        return 'VERIFIED' if module.verify(post, envelope, 'demo-one') else 'NOT_VERIFIED'
    except module.custody.CustodyUnknown:
        return 'UNKNOWN'


with tempfile.TemporaryDirectory() as temporary:
    old = pathlib.Path(temporary) / 'base.py'
    old.write_bytes(subprocess.check_output(['git', 'show', BASE + ':' + SOURCE], cwd=ROOT))
    before = observed(old)
    after = observed(ROOT / SOURCE)
    negative = observed(ROOT / SOURCE, wrong_type=True)
    print('Target: isolated source fixture; no journal, network or live producer')
    print('Same WorkItem IRI rendered with alias a:')
    print('Base ' + BASE[:12] + ': ' + before)
    print('Candidate: ' + after)
    print('Wrong type control: ' + negative)
    assert (before, after, negative) == ('UNKNOWN', 'VERIFIED', 'UNKNOWN')
