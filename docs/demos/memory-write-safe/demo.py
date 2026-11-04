"""Show actual disposable-server requests exercised by the regression tests."""
from contextlib import contextmanager
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'tests'))
import test_memory_write_safe_profiles as fixture

if not fixture.QUIPU_MEMORY_SERVER:
    raise SystemExit('QUIPU_MEMORY_SERVER_BIN is required; this demo cannot skip.')
subprocess.run([fixture.QUIPU_MEMORY_SERVER, '--version'], check=True)
original = fixture.lab


@contextmanager
def observed_lab():
    with original() as post:
        def observed(path, body):
            status, answer = post(path, body)
            if path in ('/knot', '/shapes'):
                label = body.get('name') or body['turtle'].split('>')[0] + '>'
                conforms = answer.get('conforms', 'not returned') if isinstance(answer, dict) else 'not returned'
                print(f'{path} {label}: HTTP {status}, conforms={conforms}', flush=True)
            return status, answer
        yield observed


fixture.lab = observed_lab
suite = unittest.defaultTestLoader.loadTestsFromTestCase(fixture.MemoryWritePlan)
result = unittest.TextTestRunner(verbosity=2).run(suite)
if not result.wasSuccessful() or result.skipped or result.testsRun != 2:
    raise SystemExit(1)
