"""Private-store before/after demo; no production connection is used.

Run from the repository root: python3 tests/demo_supersede_plane.py
The old promotion client is read from the pinned baseline commit. Each revision
gets its own ephemeral loopback store with the same ten target facts.
"""
import datetime
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_supersede_plane import SupersedeStoreTests, pp

BASE = '6e1556a14476273e254bbf928bcb3f0ec2e936da'


def run(mode):
    fixture = SupersedeStoreTests('test_exact_ten_fact_replacement_preserves_history_and_siblings')
    fixture.setUpClass()
    try:
        old, new, siblings, graph, source = fixture.fixture('demo')
        temp = Path(fixture.temp.name)
        new_file, old_file = temp / 'new.nt', temp / 'old.nt'
        new_file.write_text(' '.join(new) + ' .\n')
        old_file.write_text(' '.join(old) + ' .\n')
        cut = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        assert fixture.present(graph, old, valid_at=cut)
        time.sleep(1.05)
        script = pp.ROOT / 'promote_plane.py'
        if mode == 'before':
            directory = temp / 'baseline'
            directory.mkdir()
            for name in ('promote_plane.py', 'planes.py', 'quipu_auth.py'):
                content = subprocess.check_output(['git', 'show', f'{BASE}:scripts/{name}'], cwd=pp.ROOT.parent)
                (directory / name).write_bytes(content)
            script = directory / 'promote_plane.py'
        args = [sys.executable, str(script), '--triples-file', str(new_file),
                '--to', 'crew:declared', '--by', 'bob', '--authored-by', 'claude',
                '--reason', 'private demonstration']
        if mode == 'after':
            args += ['--supersedes', str(old_file)]
        env = dict(os.environ, QUIPU_SERVER=fixture.base, CAMAYOC_AUTHORITY=str(fixture.auth),
                   QUIPU_AUTH_TOKEN='isolated-test-fixture')
        result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=30)
        print(f'{mode}: promote-plane' + (' --supersedes old.nt' if mode == 'after' else ' (ordinary promotion)'))
        print(result.stdout.strip())
        if result.returncode:
            raise RuntimeError(f'private demo refused: exit{result.returncode}')
        rows = fixture.call('/query', {'query': f'SELECT ?o WHERE {{ GRAPH <{graph}> {{ {old[0]} {old[1]} ?o }} }}'})
        metrics = dict(current_claims=len(rows['rows']), old_current=fixture.present(graph, old),
                       new_current=fixture.present(graph, new), old_as_of=fixture.present(graph, old, valid_at=cut),
                       siblings_kept=sum(fixture.present(graph, t) for t in siblings),
                       inferred_source_kept=fixture.present(source, new))
        print(metrics)
        assert metrics['current_claims'] == (2 if mode == 'before' else 1)
        assert metrics['old_current'] == (mode == 'before')
        assert metrics['new_current'] and metrics['old_as_of']
        assert metrics['siblings_kept'] == 9 and metrics['inferred_source_kept']
    finally:
        fixture.tearDownClass()


if __name__ == '__main__':
    print('Synthetic private loopback stores; no production writes. Baseline ' + BASE)
    for name in ('promote_plane.py', 'planes.py'):
        print(name + ' candidate SHA256 ' + hashlib.sha256((pp.ROOT / name).read_bytes()).hexdigest())
    run('before')
    run('after')
