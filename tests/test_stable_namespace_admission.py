"""Real server proof: stable packs retain old emit/reject admission behavior."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from rdflib import Graph, RDF, RDFS, URIRef, Literal

from quipu_bin_guard import QUIPU_SERVER, requires_quipu_server

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from build_stable_namespaces import stable_bundle, mapping_ledger


@requires_quipu_server
class StableNamespaceAdmission(unittest.TestCase):
    def test_original_and_stable_pack_preserve_emit_and_reject_controls(self):
        ledger = mapping_ledger(ROOT/'namespaces/intigraph/mapping.json')
        mapping = {row['old']: row['new'] for row in ledger['pairs']}
        artifacts = stable_bundle(ROOT)
        source = (ROOT/'shapes/aegis/homelab-infra.shapes.ttl').read_text()
        stable = artifacts['camayoc/homelab-infra/ns.ttl'].decode()
        for name, shapes, migrated in [('legacy', source, False), ('stable', stable, True)]:
            with self.subTest(pack=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root/'.bobbin').mkdir()
                fixture = 'isolated-namespace-admission-fixture'
                (root/'.bobbin/config.toml').write_text(
                    '[quipu.shacl]\nvalidate_on_write = true\n'
                    '[quipu.server]\nauth_token = '+json.dumps(fixture)+'\n')
                with socket.socket() as sock:
                    sock.bind(('127.0.0.1', 0))
                    port = sock.getsockname()[1]
                base = f'http://127.0.0.1:{port}'
                env = {k: v for k, v in os.environ.items() if not k.startswith('QUIPU_')}
                env['QUIPU_AUTH_TOKEN'] = fixture

                def request(path, payload=None):
                    data = None if payload is None else json.dumps(payload).encode()
                    req = Request(base+path, data=data, headers={
                        'Content-Type': 'application/json', 'Authorization': 'Bearer '+fixture})
                    try:
                        response = urlopen(req, timeout=10)
                    except HTTPError as exc:
                        response = exc
                    with response:
                        return response.status, json.load(response)

                with (root/'server.log').open('w') as log:
                    child = subprocess.Popen([str(QUIPU_SERVER), '--db', str(root/'store.db'),
                                              '--bind', f'127.0.0.1:{port}'], cwd=root, env=env,
                                             stdout=log, stderr=log)
                    try:
                        for _ in range(100):
                            if child.poll() is not None:
                                self.fail('private server exited before health')
                            try:
                                if request('/health')[0] == 200:
                                    break
                            except (URLError, OSError):
                                time.sleep(.05)
                        else:
                            self.fail('private server never became healthy')
                        status, result = request('/shapes', {'action': 'load', 'name': 'namespace',
                                                            'turtle': shapes})
                        self.assertEqual(status, 200, result)
                        def iri(local):
                            old = 'http://aegis.gastown.local/ontology/'+local
                            return URIRef(mapping.get(old, old) if migrated else old)
                        for case, kind, accepted in [('emit', 'emit', True),
                                                     ('reject', 'reject', False),
                                                     ('positive', 'positive', True)]:
                            subject = URIRef('https://example.org/namespace/'+case)
                            graph = Graph()
                            if kind == 'emit':
                                graph.add((subject, iri('depends_on'), Literal('not an IRI')))
                            else:
                                graph.add((subject, RDF.type, iri('BareMetalHost')))
                                if kind == 'positive':
                                    graph.add((subject, RDFS.label, Literal('example host')))
                            status, result = request('/knot', {'turtle': graph.serialize(format='turtle'),
                                                              'actor': 'isolated-namespace-test'})
                            self.assertEqual(status, 200, result)
                            self.assertIs(result.get('conforms'), accepted, result)
                            status, readback = request('/query', {'query':
                                'SELECT ?p ?o WHERE { <'+str(subject)+'> ?p ?o }'})
                            self.assertEqual(status, 200, readback)
                            self.assertEqual(readback.get('count', -1) > 0, accepted, readback)
                            if not accepted:
                                self.assertEqual(readback.get('count'), 0, readback)
                    finally:
                        child.terminate()
                        child.wait(timeout=10)
                        for _ in range(100):
                            with socket.socket() as check:
                                if check.connect_ex(('127.0.0.1', port)) != 0:
                                    break
                            time.sleep(.05)
                        else:
                            self.fail('private server listener leaked')


if __name__ == '__main__':
    unittest.main()
