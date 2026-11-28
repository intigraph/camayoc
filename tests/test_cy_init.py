"""A fresh installed CLI store must support memory without manual shape loads."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from test_cy_cli import free_port, run_cy
from quipu_bin_guard import QUIPU_SERVER, requires_quipu_server
from camayoc_cli import initialize, quipu

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from init_resources import payload


class ResourceTests(unittest.TestCase):
    def test_wheel_resources_match_sources(self):
        self.assertEqual(payload(), json.loads((ROOT / 'camayoc_cli/data/init-resources.json').read_text()))

    def test_gate_requires_structured_source_kind_refusal(self):
        client = mock.Mock()
        for result in ({}, {'conforms': True}, {'conforms': False},
                       {'conforms': False, 'issues': [{'path': 'label'}]},
                       {'conforms': True, 'issues': [{'path': 'sourceKind'}]}):
            client._request.return_value = result
            with self.subTest(result=result), self.assertRaises(quipu.QuipuError):
                initialize.prove_gate(client)
        client._request.return_value = {'conforms': False, 'issues': [{'path': 'sourceKind'}]}
        initialize.prove_gate(client)
        for error in (quipu.Unreachable('offline'), quipu.QuipuError('HTTP 401')):
            client._request.side_effect = error
            with self.assertRaises(quipu.QuipuError):
                initialize.prove_gate(client)

    def test_remote_unreachable_does_not_start(self):
        client = mock.Mock(base='https://store.example')
        client.health.side_effect = quipu.Unreachable('offline')
        with tempfile.TemporaryDirectory() as project, mock.patch.object(subprocess, 'Popen') as start:
            with self.assertRaisesRegex(quipu.QuipuError, 'remote store'):
                initialize.ensure_server(client, Path(project))
            start.assert_not_called()

    def test_timeout_does_not_start(self):
        client = mock.Mock(base='http://127.0.0.1:3030')
        client.health.side_effect = quipu.TimedOut('unknown')
        with tempfile.TemporaryDirectory() as project, mock.patch.object(subprocess, 'Popen') as start:
            with self.assertRaises(quipu.TimedOut):
                initialize.ensure_server(client, Path(project))
            start.assert_not_called()


@requires_quipu_server
class InitStoreTests(unittest.TestCase):
    def test_fresh_store_rerun_and_record(self):
        self.exercise(False, fresh_token=True)

    def test_core_only_store_gets_memory(self):
        self.exercise(True)

    def exercise(self, preload, fresh_token=False):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            token = project / 'token'
            token.write_text('test-init-credential')
            env = {'QUIPU_SERVER': f'http://127.0.0.1:{free_port()}',
                   'QUIPU_AUTH_TOKEN': '', 'QUIPU_AUTH_TOKEN_FILE': '' if fresh_token else str(token),
                   'QUIPU_SERVER_BIN': QUIPU_SERVER, 'HOME': temp,
                   'XDG_STATE_HOME': str(project / 'state')}
            old = Path.cwd()
            os.chdir(project)
            try:
                if preload:
                    with mock.patch.dict(os.environ, env):
                        client = quipu.Client()
                        initialize.ensure_server(client, project)
                        client.knot((ROOT / 'ontology/core.ttl').read_text(), None, 'test', 'core-only')
                        client._request('/shapes', {'action': 'load', 'name': 'camayoc-core',
                            'turtle': (ROOT / 'shapes/core.shapes.ttl').read_text()})
                for _ in range(2):
                    code, out, err = run_cy('init', '--person', 'person-test', '--agent', 'agent-test', env=env)
                    self.assertEqual(0, code, out + err)
                    self.assertIn('gate proved', out)
                if fresh_token:
                    self.assertEqual(0o400, (project / '.config/quipu/token').stat().st_mode & 0o777)
                code, out, err = run_cy('topic', 'add', 'test-topic', env=env)
                self.assertEqual(0, code, out + err)
                code, out, err = run_cy('record', 'decision', '--by', 'person-test', '--about', 'agent-test',
                    '--topic', 'test-topic', '--chose', 'Use working memory.', env=env)
                self.assertEqual(0, code, out + err)
                self.assertNotIn('queued', out)
                code, out, err = run_cy('recall', 'decision', '--topic', 'test-topic', env=env)
                self.assertEqual(0, code, out + err)
                self.assertIn('Use working memory.', out)
            finally:
                os.chdir(old)
                pid = project / '.quipu/server.pid'
                if pid.exists():
                    os.kill(int(pid.read_text()), 15)
