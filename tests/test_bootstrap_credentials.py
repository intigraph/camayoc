"""A failed health request cannot authorize minting for existing infrastructure."""
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import bootstrap_credentials as credentials


class ProvisioningControls(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        self.env = patch.dict(os.environ, {'HOME': str(self.root / 'home')}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            self.port = listener.getsockname()[1]
        self.url = f'http://127.0.0.1:{self.port}'

    def test_remote_and_occupied_local_endpoint_cannot_mint(self):
        for url in ('http://example.org:3030', 'http://user@localhost:3030', self.url):
            with socket.socket() as listener:
                listener.bind(('127.0.0.1', self.port))
                with self.assertRaises((ValueError, OSError)):
                    credentials.provision(self.project, url)
            self.assertFalse((self.root / 'home').exists())
            self.assertFalse((self.project / '.bobbin').exists())

    def test_existing_data_or_configuration_cannot_mint(self):
        data = self.project / '.quipu'
        data.mkdir()
        with self.assertRaises(ValueError):
            credentials.provision(self.project, self.url)
        data.rmdir()
        config = self.project / '.bobbin/config.toml'
        config.parent.mkdir()
        config.write_text('existing operator config')
        with self.assertRaises(ValueError):
            credentials.provision(self.project, self.url)
        self.assertEqual(config.read_text(), 'existing operator config')
        self.assertFalse((self.root / 'home').exists())

    def test_explicit_environment_credential_is_preserved(self):
        os.environ['QUIPU_AUTH_TOKEN'] = 'private-fixture'
        credentials.provision(self.project, self.url)
        self.assertIn('auth_token = "private-fixture"',
                      (self.project / '.bobbin/config.toml').read_text())
        self.assertFalse((self.root / 'home').exists())

    def test_empty_explicit_credential_file_refuses_mint(self):
        path = self.root / 'empty-token'
        path.touch()
        os.environ['QUIPU_AUTH_TOKEN_FILE'] = str(path)
        with self.assertRaises(ValueError):
            credentials.provision(self.project, self.url)
        self.assertEqual(path.read_bytes(), b'')
        self.assertFalse((self.project / '.bobbin/config.toml').exists())
