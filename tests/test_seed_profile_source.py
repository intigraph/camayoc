"""External Seeds data is pinned, bounded and resolved before any installer write."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('seed_profile', ROOT/'scripts/seed_profile.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class Source(unittest.TestCase):
    def contract(self, directory, **changes):
        body = b'@prefix x: <https://example.org/> . x:s x:p x:o .'
        data = {'repository': 'scbrown/seeds', 'commit': 'a'*40,
                'profiles': [{'path': 'shapes/seeds.shapes.ttl',
                              'sha256': hashlib.sha256(body).hexdigest()}], **changes}
        path = Path(directory)/'contract.json'
        path.write_text(json.dumps(data))
        return path, body

    def test_exact_remote_bytes_and_changed_hash_refusal(self):
        with tempfile.TemporaryDirectory() as directory:
            path, body = self.contract(directory)
            urls = []
            def fetch(url): urls.append(url); return body
            with patch.object(m, 'CONTRACT', path):
                self.assertEqual(m.profiles(fetch=fetch)[0][1], body.decode())
                self.assertIn('/'+'a'*40+'/', urls[0])
                with self.assertRaises(ValueError): m.profiles(fetch=lambda _: body+b'changed')

    def test_wrong_owner_mutable_ref_and_path_refused_before_fetch(self):
        with tempfile.TemporaryDirectory() as directory:
            for changes in ({'repository': 'other/repo'}, {'commit': 'main'},
                            {'profiles': [{'path': '../other', 'sha256': 'b'*64}]}):
                path, _ = self.contract(directory, **changes)
                with patch.object(m, 'CONTRACT', path):
                    with self.assertRaises(ValueError):
                        m.profiles(fetch=lambda _: self.fail('unexpected fetch'))

    def test_local_source_is_checked_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            path, body = self.contract(directory)
            source = Path(directory)/'source';(source/'shapes').mkdir(parents=True)
            (source/'shapes/seeds.shapes.ttl').write_bytes(body)
            with patch.object(m, 'CONTRACT', path):
                self.assertEqual(len(m.profiles(source=source)), 1)
                (source/'shapes/seeds.shapes.ttl').write_bytes(b'wrong')
                with self.assertRaises(ValueError): m.profiles(source=source)


if __name__ == '__main__': unittest.main()
