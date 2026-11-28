"""Provision working memory; no successful readiness claim without a gate verdict."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import time
from urllib.parse import urlsplit

from . import memory, planes, quipu


def ensure_server(client, project):
    try:
        client.health(timeout=2)
        return
    except quipu.TimedOut:
        raise  # A timeout cannot establish that this endpoint is unowned.
    except quipu.Unreachable:
        pass
    endpoint = urlsplit(client.base)
    if (endpoint.scheme != 'http' or endpoint.hostname not in ('localhost', '127.0.0.1')
            or endpoint.username or endpoint.password or endpoint.path not in ('', '/')
            or endpoint.query or endpoint.fragment):
        raise quipu.QuipuError('Start the remote store separately; init starts only loopback stores.')
    binary = os.environ.get('QUIPU_SERVER_BIN') or shutil.which('quipu-server')
    if not binary:
        raise quipu.QuipuError('Install quipu-server, then run cy init again.')
    config = project / '.bobbin/config.toml'
    state = project / '.quipu'
    if config.parent.is_symlink() or state.is_symlink() or config.is_symlink():
        raise quipu.QuipuError('Local configuration and state must not be symlinks.')
    port = endpoint.port or 3030
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', port))
    if not config.exists():
        if state.exists():
            raise quipu.QuipuError('Existing local state has no configuration; preserve it and configure the server.')
        bearer = quipu.token()
        if not bearer:
            path = Path(os.environ.get('QUIPU_AUTH_TOKEN_FILE') or Path.home() / '.config/quipu/token')
            if os.environ.get('QUIPU_AUTH_TOKEN_FILE') or path.exists() or path.is_symlink():
                raise quipu.QuipuError('Configured credential is empty; repair it before init.')
            if any(p.is_symlink() for p in path.parents):
                raise quipu.QuipuError('Credential directories must not be symlinks.')
            path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            bearer = secrets.token_urlsafe(32)
            with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400), 'w') as out:
                out.write(bearer + '\n')
        ignore = project / '.gitignore'
        if ignore.is_symlink():
            raise quipu.QuipuError('Project gitignore must not be a symlink.')
        with ignore.open('a') as out:
            out.write('\n# Private governed memory\n/.bobbin/config.toml\n/.quipu/\n')
        config.parent.mkdir(parents=True, exist_ok=True)
        with os.fdopen(os.open(config, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as out:
            out.write('[quipu]\nbase_ns = ' + json.dumps(memory.ns()) + '\n'
                      '[quipu.shacl]\nvalidate_on_write = true\n[quipu.server]\n'
                      'bind = ' + json.dumps(f'127.0.0.1:{port}') + '\n'
                      'auth_token = ' + json.dumps(bearer) + '\n')
    state.mkdir(exist_ok=True)
    with (state / 'server.log').open('ab') as log:
        proc = subprocess.Popen([binary, '--db', str(state / 'store.db'), '--bind', f'127.0.0.1:{port}'],
                                cwd=project, stdin=subprocess.DEVNULL, stdout=log,
                                stderr=subprocess.STDOUT, start_new_session=True)
    (state / 'server.pid').write_text(str(proc.pid) + '\n')
    for _ in range(50):
        if proc.poll() is not None:
            break
        try:
            client.health(timeout=0.5)
            return
        except quipu.Unreachable:
            time.sleep(0.1)
    raise quipu.QuipuError('Local store did not start; inspect .quipu/server.log. State was preserved.')


def checked(client, path, body):
    result = client._request(path, body)
    if result.get('error') or result.get('conforms') is False:
        raise quipu.QuipuError(f'{path} refused setup; readiness unproved')
    return result


def load_vocabulary(client, text, source):
    prefixes = dict(re.findall(r'^@prefix\s+([\w-]*):\s*<([^>]+)>\s*\.', text, re.M))
    terms = sorted({prefixes[p] + n for p, n in re.findall(r'^([\w-]+):([\w-]+)\s+a\s+', text, re.M)})
    values = ' '.join(memory.iri_ref(i) for i in terms)
    rows = client.query(f'SELECT (COUNT(DISTINCT ?s) AS ?n) WHERE {{ VALUES ?s {{ {values} }} ?s a ?t }}')
    if not rows:
        raise quipu.QuipuError('Vocabulary presence is unknown; no setup write attempted.')
    if int(quipu.value(rows[0]['n'])) != len(terms):
        checked(client, '/ontology', {'action': 'load', 'name': 'camayoc-' + Path(source).stem,
                                      'turtle': text})


def run(person, agent, project=None):
    # Validate all names before starting or writing anything.
    identities = [(memory.to_iri(person), 'Person', person),
                  (memory.to_iri(agent), 'Principal', agent)]
    if identities[0][0] == identities[1][0]:
        raise quipu.QuipuError('Choose distinct person and agent identities.')
    resources = json.loads((Path(__file__).parent / 'data/init-resources.json').read_text())
    client = quipu.Client()
    ensure_server(client, Path(project or Path.cwd()).resolve())
    if not quipu.has_token():
        raise quipu.QuipuError('Configure an accepted Quipu credential before loading memory.')
    for path in ('ontology/core.ttl', 'ontology/agent-memory.ttl'):
        load_vocabulary(client, resources[path], path)
    for path in ('shapes/core.shapes.ttl', 'shapes/agent-memory.shapes.ttl',
                 'shapes/observation-debt.shapes.ttl'):
        checked(client, '/shapes', {'action': 'load', 'name': {'core.shapes.ttl': 'camayoc-core',
                                             'agent-memory.shapes.ttl': 'agent-memory',
                                             'observation-debt.shapes.ttl': 'camayoc-observation-debt'}[Path(path).name],
                                    'turtle': resources[path]})
    # Reuse the existing owner types, also making them admissible in a fresh store.
    a = memory.ns()
    identity_shapes = ''.join(
        f'<{a}Init{kind}Shape> a <http://www.w3.org/ns/shacl#NodeShape> ; '
        f'<http://www.w3.org/ns/shacl#targetClass> <{a}{kind}> .\n'
        for kind in ('Person', 'Principal'))
    checked(client, '/shapes', {'action': 'load', 'name': 'camayoc-init-identities', 'turtle': identity_shapes})
    now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    for kind, rank, trust in (('observed', 20, 'high'), ('declared', 30, 'human-root'), ('inferred', 0, 'low')):
        graph = planes.plane_for(kind)
        checked(client, '/graph/create', {'graph': graph})
        checked(client, '/graph/label', {'graph': graph, 'timestamp': now, 'actor': 'camayoc-init',
            'kind': 'knowledge', 'trust': {'iri': planes.plane_ns() + 'trust/' + trust,
            'chain': planes.plane_ns() + 'chain/provenance', 'rank': rank}})
    for path, text in resources.items():
        if path.startswith('queries/'):
            checked(client, '/queries', json.loads(text))
    for iri, kind, label in identities:
        client.knot(f'{memory.iri_ref(iri)} a <{a}{kind}> ; '
                    f'<http://www.w3.org/2000/01/rdf-schema#label> {memory.lit(label)} ; '
                    f'<{a}sourceKind> "declared" .', planes.plane_for('declared'), 'camayoc-init', 'cy init')
        if not client.query(f'SELECT ?s FROM <{planes.plane_for("declared")}> WHERE {{ '
                            f'BIND({memory.iri_ref(iri)} AS ?s) ?s a <{a}{kind}> }} LIMIT 1'):
            raise quipu.QuipuError('Identity read-back failed; readiness unproved.')
    prove_gate(client)
    return memory.Outcome(0, ['camayoc: working memory ready; gate proved (missing sourceKind refused).',
                              f'Person: {identities[0][0]}', f'Agent: {identities[1][0]}'])


def prove_gate(client):
    a = memory.ns()
    # Structured SHACL refusal only: transport/auth/vocabulary errors never prove the gate.
    probe = client._request('/knot', {'turtle': f'<urn:camayoc:init:gate> a <{a}Decision> .',
        'graph': planes.plane_for('declared'), 'actor': 'camayoc-init', 'source': 'cy init gate'})
    issues = probe.get('issues') or []
    if probe.get('conforms') is not False or not any('sourceKind' in str(i.get('path', '')) for i in issues):
        raise quipu.QuipuError('Gate unproved: store must refuse the probe for missing sourceKind; setup may be partial.')
