#!/usr/bin/env python3
"""Build the CLI wheel from the extracted, verified release source (Python 3.11+)."""
# arming: ci release-proof builds and installs the actual artifact before publication
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import venv


def release_version(source, proof):
    """All shipped interfaces must identify the same stable release."""
    version = proof['version']
    project = tomllib.loads((source / 'pyproject.toml').read_text())['project']
    tree = ast.parse((source / 'camayoc_cli/__init__.py').read_text())
    cli_versions = [ast.literal_eval(node.value) for node in tree.body
                    if isinstance(node, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == '__version__' for t in node.targets)]
    plugin = json.loads((source / '.claude-plugin/plugin.json').read_text())
    marketplace = json.loads((source / '.claude-plugin/marketplace.json').read_text())
    versions = [project['version'], *cli_versions, plugin['version'],
                *[p['version'] for p in marketplace['plugins']]]
    if len(versions) != 4 or any(v != version for v in versions) or proof['tag'] != 'v' + version:
        raise ValueError('release version disagrees across Python and plugin metadata')
    if project.get('dependencies'):
        raise ValueError('CLI wheel must remain runtime dependency-free')
    return version


def build(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    proof_path = output / 'source.json'
    proof = json.loads(proof_path.read_text())
    version = release_version(source, proof)
    wheel = output / f'camayoc-{version}-py3-none-any.whl'
    if wheel.exists() or 'wheel' in proof:
        raise ValueError('wheel output already exists')
    with tempfile.TemporaryDirectory(prefix='camayoc-wheel-') as temporary:
        stage = Path(temporary)
        subprocess.run([sys.executable, '-m', 'pip', 'wheel', '--no-deps',
                        '--wheel-dir', str(stage), str(source)], check=True)
        built = stage / wheel.name
        if list(stage.glob('*.whl')) != [built]:
            raise ValueError('unexpected wheel name or extra wheel')
        environment = stage / 'installed'
        venv.EnvBuilder(with_pip=True).create(environment)
        python = environment / 'bin/python'
        subprocess.run([str(python), '-m', 'pip', 'install', '--no-index', '--no-deps', str(built)],
                       cwd=stage, check=True)
        for command in ('cy', 'camayoc'):
            executable = environment / 'bin' / command
            result = subprocess.run([str(executable), '--version'], cwd=stage,
                                    check=True, capture_output=True, text=True)
            if result.stdout.strip() != 'camayoc ' + version:
                raise ValueError('installed CLI version mismatch')
            # Exercise packaged primer and generated definitions away from the checkout.
            for args in (['prime', '--static'], ['record', 'decision', '--help']):
                subprocess.run([str(executable), *args], cwd=stage, check=True,
                               capture_output=True, text=True)
        wheel.write_bytes(built.read_bytes())
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    proof['wheel'] = {'artifact': wheel.name, 'sha256': digest, 'format': 'python-wheel'}
    proof_path.write_text(json.dumps(proof, sort_keys=True, indent=2) + '\n')
    with (output / 'SHA256SUMS').open('a') as checksums:
        checksums.write(digest + '  ' + wheel.name + '\n')
    return proof


if __name__ == '__main__':
    if len(sys.argv) != 3:
        raise SystemExit('usage: build_release_wheel.py <extracted-source> <release-dir>')
    print(json.dumps(build(*sys.argv[1:]), sort_keys=True))
