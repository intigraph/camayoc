"""Compare actual record() output on the parent and current source; no network."""
import io
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[3]
CODE = '''
from unittest.mock import patch
from camayoc_cli import memory
class Writer:
    def knot(self, turtle, graph, actor, source):
        print('Typed deadline emitted:', 'https://w3id.org/intigraph/quechua/ns#traceabilityDueAt' in turtle and '2030-11-28T08:00:00Z' in turtle)
with patch.object(memory, 'entity_exists', return_value=True), patch.object(memory, 'find_topic', return_value='urn:test:topic'), patch.object(memory, 'current_versions', return_value=[]):
    result = memory.record('directive', dict(rule='Use the reviewed path.', issuedBy='urn:test:operator', appliesTo='crew', topic='release-path', traceabilityDueAt='2030-11-28T08:00:00Z'), client=Writer())
    print('record exit:', result.code)
    assert result.code == 0, result.lines
'''
with tempfile.TemporaryDirectory() as directory:
    data = subprocess.check_output(['git', 'archive', '91bef07f8862d11a89730ee2727d2bff21ca4d86'], cwd=ROOT)
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        archive.extractall(directory, filter='data')
    for label, source in [('BEFORE (parent source)', Path(directory)), ('AFTER (candidate source)', ROOT)]:
        print(label, flush=True)
        subprocess.run([sys.executable, '-c', CODE], cwd=source,
                       env={**os.environ, 'PYTHONPATH':str(source)}, check=True)
