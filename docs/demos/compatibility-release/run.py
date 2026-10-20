"""Compare the old and new producer on one immutable source revision."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[3]

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = ROOT
    return module

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--base', default='ba1cb3c7f01303e8930cdd58e229bcd39dabcdf0')
parser.add_argument('--ref', default='HEAD')
args = parser.parse_args()
def git(*values):
    return subprocess.check_output(['git', '-C', str(ROOT), *values])
revision = git('rev-parse', args.ref + '^{commit}').decode().strip()
version = json.loads(git('show', revision + ':.claude-plugin/plugin.json'))['version']
print('Target: immutable local source archive; no release publication or installation.', flush=True)
print('Shared input revision:', revision, flush=True)
with tempfile.TemporaryDirectory(prefix='compatibility-release-demo-') as temporary:
    root = Path(temporary)
    baseline = root / 'base.py'; baseline.write_bytes(git('show', args.base + ':scripts/package_release.py'))
    candidate = root / 'candidate.py'; candidate.write_bytes(git('show', revision + ':scripts/package_release.py'))
    print('Base producer:', args.base, flush=True)
    print('Candidate producer SHA256:', hashlib.sha256(candidate.read_bytes()).hexdigest(), flush=True)
    for label, module in [('before', load('old_release', baseline)), ('after', load('new_release', candidate))]:
        proof = module.package(revision, version, root / label)
        with tarfile.open(root / label / proof['artifact']) as archive:
            files = [n for n in archive.getnames() if '/compatibility/aegis/' in n]
            bootstrap = archive.getmember('camayoc-v' + version + '/scripts/bootstrap.sh').isfile()
            print(label, 'compatibility_files=', len(files), 'bootstrap_positive=', bootstrap, flush=True)
            if label == 'before':
                assert not files and 'compatibility' not in proof
            else:
                content = archive.extractfile('camayoc-v' + version + '/' + proof['compatibility']['path']).read()
                manifest = json.loads(content)
                assert len(files) == 7 and manifest['node_shapes'] == 120 and manifest['union_isomorphic']
                assert hashlib.sha256(content).hexdigest() == proof['compatibility']['sha256']
                print('after manifest:', json.dumps({k: manifest[k] for k in ('node_shapes', 'union_isomorphic', 'automatic_load')}, sort_keys=True), flush=True)
                print('after manifest_sha256:', proof['compatibility']['sha256'], flush=True)
