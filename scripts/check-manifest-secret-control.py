#!/usr/bin/env python3
"""Check the exact manifest exception and a new secret in that same file.

Requires git and gitleaks. All mutations occur in a disposable local clone.
No fixture value or scanner report is printed.
"""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
COMMIT = '18d7d7fef456e691b542a890b138d31758c4d85e'
MANIFEST = 'docs/demos/terminal/manifest.json'


def run(*args, cwd, expected=0):
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if result.returncode != expected:
        raise SystemExit(f'{args[0]} returned {result.returncode}; expected {expected} (output withheld)')
    return result


def scan(tree, report, revision, expected):
    run('gitleaks', 'git', '--no-banner', '--redact', '--report-format', 'json',
        '--report-path', str(report), '--log-opts', revision, str(tree),
        cwd=tree, expected=expected)
    return json.loads(report.read_text())


def main():
    with tempfile.TemporaryDirectory(prefix='manifest-secret-control-') as scratch:
        scratch = Path(scratch)
        tree = scratch / 'repo'
        run('git', 'clone', '--shared', '--no-checkout', str(ROOT), str(tree), cwd=scratch)
        run('git', 'checkout', '--detach', COMMIT, cwd=tree)
        # Use the proposed exception, rather than the historical checkout's file.
        shutil.copyfile(ROOT / '.gitleaksignore', tree / '.gitleaksignore')
        findings = scan(tree, scratch / 'digest.json', f'{COMMIT}^..{COMMIT}', 0)
        if findings:
            raise SystemExit('Known file digest was not excluded')
        # Public synthetic pattern; generated to avoid embedding token-like text in source.
        fake = 'gh' + 'p_' + 'Ab3Cd4Ef5Gh6Ij7Kl8Mn9Op0Qr1St2Uv3Wx4'
        path = tree / MANIFEST
        data = json.loads(path.read_text())
        data['api_key'] = fake
        path.write_text(json.dumps(data, indent=2) + '\n')
        run('git', 'add', MANIFEST, cwd=tree)
        run('git', '-c', 'core.hooksPath=/dev/null', '-c', 'user.name=Fixture',
            '-c', 'user.email=fixture@example.test', 'commit', '-m', 'Synthetic scanner control', cwd=tree)
        findings = scan(tree, scratch / 'positive.json', 'HEAD^..HEAD', 1)
        if not any(f.get('File') == MANIFEST and f.get('RuleID') == 'github-pat' for f in findings):
            raise SystemExit('Same-file synthetic secret was not detected')
        data['api_key'] = hashlib.sha256(b'public synthetic generic API key fixture').hexdigest()
        path.write_text(json.dumps(data, indent=2) + '\n')
        run('git', 'add', MANIFEST, cwd=tree)
        run('git', '-c', 'core.hooksPath=/dev/null', '-c', 'user.name=Fixture',
            '-c', 'user.email=fixture@example.test', 'commit', '-m', 'Synthetic generic API key control', cwd=tree)
        findings = scan(tree, scratch / 'generic.json', 'HEAD^..HEAD', 1)
        if not any(f.get('File') == MANIFEST and f.get('RuleID') == 'generic-api-key' for f in findings):
            raise SystemExit('Same-file generic API key was not detected')
        print('PASS: exact digest excluded; same-file PAT and generic API key detected')


if __name__ == '__main__':
    main()
