"""Refuse split release identities before invoking the build backend."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('release_wheel', Path(__file__).resolve().parents[1] / 'scripts/build_release_wheel.py')
WHEEL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WHEEL)


class WheelIdentity(unittest.TestCase):
    def test_current_source_identity_and_each_mismatch(self):
        root = Path(__file__).resolve().parents[1]
        names = ('pyproject.toml', 'camayoc_cli/__init__.py', '.claude-plugin/plugin.json',
                 '.claude-plugin/marketplace.json')
        version = json.loads((root / names[2]).read_text())['version']
        proof = {'version': version, 'tag': 'v' + version}
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            for name in names:
                destination = source / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes((root / name).read_bytes())
            self.assertEqual(WHEEL.release_version(source, proof), version)
            for name in names:
                path = source / name
                original = path.read_text()
                path.write_text(original.replace(version, '99.0.0'))
                with self.subTest(name=name), self.assertRaises(ValueError):
                    WHEEL.release_version(source, proof)
                path.write_text(original)
            with self.assertRaises(ValueError):
                WHEEL.release_version(source, {**proof, 'tag': 'v99.0.0'})
