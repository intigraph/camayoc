"""The released source must bind a revision, not a dirty checkout."""
import hashlib
import importlib.util
import json
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("package_release", Path(__file__).resolve().parents[1] / "scripts/package_release.py")
PACK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PACK)


class SourceRelease(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.old = PACK.ROOT
        PACK.ROOT = self.repo
        self.git("init", "-q")
        self.git("config", "user.name", "Release test")
        self.git("config", "user.email", "test@example.invalid")
        (self.repo / ".claude-plugin").mkdir()
        (self.repo / ".claude-plugin/plugin.json").write_text('{"version":"0.1.0"}')
        (self.repo / ".claude-plugin/marketplace.json").write_text('{"plugins":[{"version":"0.1.0"}]}')
        (self.repo / "bootstrap.sh").write_text("#!/bin/sh\nexit 0\n")
        (self.repo / "bootstrap.sh").chmod(0o755)
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")

    def tearDown(self):
        PACK.ROOT = self.old
        self.temp.cleanup()

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args])

    def test_source_revision_modes_and_checksums_ignore_checkout_dirt(self):
        (self.repo / "bootstrap.sh").write_text("dirty bytes")
        (self.repo / "untracked.txt").write_text("not source")
        output = self.root / "release"
        proof = PACK.package("HEAD", "0.1.0", output)
        archive = output / proof["artifact"]
        self.assertEqual(proof["source_revision"], self.git("rev-parse", "HEAD").decode().strip())
        self.assertEqual(proof["sha256"], hashlib.sha256(archive.read_bytes()).hexdigest())
        subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=output, check=True, capture_output=True)
        with tarfile.open(archive) as bundle:
            names = bundle.getnames()
            self.assertNotIn("camayoc-v0.1.0/untracked.txt", names)
            member = bundle.getmember("camayoc-v0.1.0/bootstrap.sh")
            self.assertEqual(member.mode, 0o775)
            self.assertEqual(bundle.extractfile(member).read(), b"#!/bin/sh\nexit 0\n")
        self.assertEqual(json.loads((output / "source.json").read_text()), proof)
        original = archive.read_bytes()
        archive.write_bytes(original + b"tampered")
        self.assertNotEqual(subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=output, capture_output=True).returncode, 0)
        archive.unlink()
        self.assertNotEqual(subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=output, capture_output=True).returncode, 0)

    def test_same_revision_repeats_identical_bytes(self):
        one = PACK.package("HEAD", "0.1.0", self.root / "one")
        two = PACK.package("HEAD", "0.1.0", self.root / "two")
        self.assertEqual(one, two)
        self.assertEqual((self.root / "one" / one["artifact"]).read_bytes(), (self.root / "two" / two["artifact"]).read_bytes())

    def test_wrong_version_and_occupied_output_refuse(self):
        for version in ("0.2.0", "../0.1.0", "0.1.0-rc1"):
            with self.assertRaises(ValueError):
                PACK.package("HEAD", version, self.root / "bad")
        occupied = self.root / "occupied"
        occupied.mkdir()
        (occupied / "keep").write_text("retained")
        with self.assertRaises(ValueError):
            PACK.package("HEAD", "0.1.0", occupied)
        self.assertEqual((occupied / "keep").read_text(), "retained")
