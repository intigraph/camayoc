"""Exercise the actual post-state writer, not only standalone validation."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.request

from quipu_bin_guard import QUIPU_SERVER, requires_quipu_server

ROOT = Path(__file__).resolve().parents[1]
A = 'http://aegis.gastown.local/ontology/'
C = 'https://w3id.org/intigraph/camayoc/core/ns#'
Q = 'https://w3id.org/intigraph/quipu/ns#'
R = 'http://www.w3.org/2000/01/rdf-schema#'


@contextmanager
def lab():
    with tempfile.TemporaryDirectory(prefix='memory-write-plan-') as directory:
        root = Path(directory)
        (root / '.bobbin').mkdir()
        (root / '.bobbin/config.toml').write_text(
            '[quipu.shacl]\nvalidate_on_write = true\n'
            '[quipu.embedding]\nauto_embed = false\n')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        env = {k: v for k, v in os.environ.items() if not k.startswith('QUIPU_')}
        with (root / 'server.log').open('w+') as log:
            proc = subprocess.Popen([QUIPU_SERVER, '--db', str(root/'store.db'),
                                     '--bind', f'127.0.0.1:{port}'], cwd=root,
                                    env=env, stdout=log, stderr=log)

            def post(path, body):
                req = urllib.request.Request(base+path, json.dumps(body).encode(), headers={
                    'Content-Type': 'application/json', 'X-Quipu-Client': 'agent-adhoc'})
                try:
                    with urllib.request.urlopen(req, timeout=15) as response:
                        return response.status, json.load(response)
                except urllib.error.HTTPError as error:
                    return error.code, error.read().decode()

            try:
                for _ in range(100):
                    try:
                        post('/health', {})
                        break
                    except OSError:
                        if proc.poll() is not None:
                            log.seek(0)
                            raise AssertionError(log.read())
                        time.sleep(.05)
                else:
                    raise AssertionError('isolated server did not start')
                yield post
            finally:
                proc.terminate()
                proc.wait(timeout=10)


def record(name, family=A, rule=True):
    subject = f'<urn:test:{name}>'
    values = [('naturalKey', '<urn:test:key:'+name+'>'), ('sourceKind', '"declared"'),
              ('recordedAt', '"2026-11-04T00:00:00Z"^^<http://www.w3.org/2001/XMLSchema#dateTime>'),
              ('recordedBy', '"fixture"'), ('contentHash', '"sha256:'+'0'*64+'"'),
              ('issuedBy', '<urn:test:issuer>'), ('appliesTo', '<urn:test:scope>'),
              ('topic', '<urn:test:topic>')]
    if rule:
        values.append(('rule', '"Use the reviewed route"'))
    triples = [f'{subject} a <{family}Directive> .', f'{subject} <{R}label> "{name}" .']
    for field, value in values:
        namespace = Q if family == C and field == 'issuedBy' else family
        triples.append(f'{subject} <{namespace}{field}> {value} .')
    return '\n'.join(triples)


@requires_quipu_server
class MemoryWritePlan(unittest.TestCase):
    def test_old_alternative_cardinality_blocks_an_unrelated_write(self):
        with lab() as post:
            probe = {'turtle': '<urn:test:probe> <'+R+'label> "before" .', 'actor': 'fixture'}
            self.assertEqual(post('/knot', probe)[0], 200)
            shape = ('@prefix sh: <http://www.w3.org/ns/shacl#> . '
                     '<urn:test:shape> a sh:NodeShape; sh:targetClass <urn:test:unused>; '
                     'sh:property [sh:path [sh:alternativePath (<urn:test:p> <urn:test:q>)]; '
                     'sh:maxCount 1] .')
            self.assertEqual(post('/shapes', {'action': 'load', 'name': 'old-control', 'turtle': shape})[0], 200)
            status, body = post('/knot', probe)
            self.assertEqual(status, 400, body)
            self.assertIn('post-state value-set', str(body))

    def test_replacements_allow_writes_and_keep_record_constraints(self):
        with lab() as post:
            # Production already sanctions legacy Directive; an empty lab does
            # not. Declare both class spellings without changing their memory
            # constraints or performing an ontology/materialization load.
            declarations = ('@prefix sh: <http://www.w3.org/ns/shacl#> . '
                            '<urn:test:directive-vocabulary> a sh:NodeShape; '
                            f'sh:targetClass <{A}Directive>, <{C}Directive> .')
            self.assertEqual(post('/shapes', {'action': 'load', 'name': 'fixture-vocabulary',
                                             'turtle': declarations})[0], 200)
            for filename in ['02-shapes-new-classes.json', '03-shape-directive-scoped.json']:
                proposal = json.loads((ROOT/'docs/design/proposals/agent-memory'/filename).read_text())
                self.assertEqual(post('/shapes', {'action': 'load', 'name': proposal['target'],
                                                 'turtle': proposal['diff']})[0], 200)
            cases = [('legacy', record('legacy'), 200), ('canonical', record('canonical', C), 200),
                     ('mixed', record('mixed').replace('<'+A+'rule>', '<'+C+'rule>'), 200),
                     ('missing', record('missing', rule=False), 400),
                     ('twice', record('twice')+'\n<urn:test:twice> <'+A+'rule> "Second" .', 400),
                     ('dual', record('dual')+'\n<urn:test:dual> <'+C+'rule> "Second" .', 200),
                     ('dual-identical', record('dual-identical')+'\n<urn:test:dual-identical> <'+C+'rule> "Use the reviewed route" .', 200),
                     ('wrong-type', record('wrong-type').replace('"Use the reviewed route"', '<urn:test:not-text>'), 400),
                     ('old-directive', '<urn:test:old> a <'+A+'Directive> .', 200)]
            for field in ['label', 'sourceKind', 'recordedAt', 'recordedBy', 'contentHash',
                          'issuedBy', 'appliesTo', 'topic']:
                name = 'missing-'+field
                predicate = (R if field == 'label' else A)+field
                turtle = '\n'.join(line for line in record(name).splitlines()
                                   if '<'+predicate+'>' not in line)
                cases.append((name, turtle, 400))
            for label, turtle, expected in cases:
                with self.subTest(case=label):
                    status, body = post('/knot', {'turtle': turtle, 'actor': 'fixture'})
                    # Semantic SHACL refusal is HTTP200/conforms:false; unlike
                    # the unsafe-path400 it is a successful validation result.
                    self.assertEqual(status, 200, body)
                    self.assertEqual(body.get('conforms', True), expected == 200, body)
                    self.assertNotIn('unknown rdf:type', str(body))
                    if expected != 200:
                        _, after = post('/query', {'query': f'SELECT ?p ?o WHERE {{ <urn:test:{label}> ?p ?o }}'})
                        self.assertEqual(after['rows'], [], after)
                    # Failed writes must not poison unrelated producers.
                    status, body = post('/knot', {'turtle': f'<urn:test:probe:{label}> <{R}label> "ok" .', 'actor': 'fixture'})
                    self.assertEqual(status, 200, body)
            # Bounds are per predicate. Cross-alias union cardinality is NOT
            # guaranteed by this workaround and remains a production gate.
            status, body = post('/knot', {'turtle': f'<urn:test:legacy> <{C}rule> "Later conflict" .', 'actor': 'fixture'})
            self.assertEqual(status, 200, body)
            self.assertIs(body.get('conforms'), True, body)
            # A plain shared-record field still enforces its post-state bound.
            status, body = post('/knot', {'turtle': f'<urn:test:legacy> <{A}recordedBy> "another writer" .', 'actor': 'fixture'})
            self.assertEqual(status, 400, body)
            self.assertIn('MaxCountConstraintComponent', str(body))
            _, retained = post('/query', {'query': f'SELECT ?value WHERE {{ <urn:test:legacy> <{A}recordedBy> ?value }}'})
            self.assertEqual(retained['rows'], [{'value': 'fixture'}])
