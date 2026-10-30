"""The released source must bind a revision, not a dirty checkout."""
import gzip
import hashlib
import importlib.util
import json
import subprocess
import sys
import shutil
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

    def test_context_consumer_and_required_helper_travel_in_source_archive(self):
        source = Path(__file__).resolve().parents[1] / "scripts"
        scripts = self.repo / "scripts"
        scripts.mkdir()
        for name in ("entity_link.py", "jev.py", "search_context.py"):
            shutil.copy2(source / name, scripts / name)
        self.git("add", "scripts")
        self.git("commit", "-qm", "context consumer fixture")
        proof = PACK.package("HEAD", "0.1.0", self.root / "context-release")
        extracted = self.root / "extracted"
        extracted.mkdir()
        with tarfile.open(self.root / "context-release" / proof["artifact"]) as bundle:
            bundle.extractall(extracted, filter="data")
        installed = extracted / "camayoc-v0.1.0/scripts"
        self.assertEqual((installed / "search_context.py").read_bytes(), (source / "search_context.py").read_bytes())
        result = subprocess.run([sys.executable, str(installed / "entity_link.py"), "--help"], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        (installed / "search_context.py").unlink()
        missing = subprocess.run([sys.executable, str(installed / "entity_link.py"), "--help"], capture_output=True, text=True, timeout=10)
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("search_context", missing.stderr)

    def test_compatibility_travels_in_the_existing_source_release(self):
        source = Path(__file__).resolve().parents[1]
        for relative in ('shapes/aegis', 'tests/fixtures/aegis-upstream'):
            shutil.copytree(source / relative, self.repo / relative)
        (self.repo / 'scripts').mkdir()
        shutil.copy2(source / 'scripts/build_aegis_compatibility.py', self.repo / 'scripts/build_aegis_compatibility.py')
        self.git('add', '.')
        self.git('commit', '-qm', 'pinned compatibility inputs')
        proof = PACK.package('HEAD', '0.1.0', self.root / 'compatibility-release')
        self.assertEqual(proof['compatibility']['format'], 'aegis-compatibility-source/v1')
        with tarfile.open(self.root / 'compatibility-release' / proof['artifact']) as archive:
            manifest_bytes = archive.extractfile('camayoc-v0.1.0/' + proof['compatibility']['path']).read()
            self.assertEqual(hashlib.sha256(manifest_bytes).hexdigest(), proof['compatibility']['sha256'])
            manifest = json.loads(manifest_bytes)
            self.assertEqual(manifest['node_shapes'], 120)
            self.assertTrue(manifest['union_isomorphic'])
            self.assertFalse(manifest['automatic_load'])
            self.assertEqual(archive.pax_headers['comment'], proof['source_revision'])
            for row in manifest['files']:
                content = archive.extractfile('camayoc-v0.1.0/compatibility/aegis/' + row['path']).read()
                self.assertEqual(hashlib.sha256(content).hexdigest(), row['sha256'])
        # The existing one producer still emits identical bytes from the pinned Git revision.
        (self.repo / 'tests/fixtures/aegis-upstream/original-aegis-ontology.ttl').write_text('dirty')
        repeated = PACK.package('HEAD', '0.1.0', self.root / 'repeated-compatibility-release')
        self.assertEqual(proof, repeated)

    def test_stable_namespace_bundle_is_carried_and_ignores_checkout_dirt(self):
        source = Path(__file__).resolve().parents[1]
        for relative in ('ontology', 'shapes', 'namespaces/intigraph'):
            shutil.copytree(source/relative, self.repo/relative)
        (self.repo/'scripts').mkdir()
        for file in ('build_stable_namespaces.py', 'build_aegis_compatibility.py'):
            shutil.copy2(source/'scripts'/file, self.repo/'scripts'/file)
        self.git('add', '.')
        self.git('commit', '-qm', 'namespace source fixture')
        one = PACK.package('HEAD', '0.1.0', self.root/'namespace-one')
        (self.repo/'namespaces/intigraph/mapping.json').write_text('dirty bytes')
        two = PACK.package('HEAD', '0.1.0', self.root/'namespace-two')
        self.assertEqual(one, two)
        self.assertEqual((self.root/'namespace-one'/one['artifact']).read_bytes(),
                         (self.root/'namespace-two'/two['artifact']).read_bytes())
        with tarfile.open(self.root/'namespace-one'/one['artifact']) as archive:
            proof = one['stable_namespaces']
            raw = archive.extractfile('camayoc-v0.1.0/'+proof['path']).read()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), proof['sha256'])
            manifest = json.loads(raw)
            self.assertEqual(manifest['pair_count'], 1254 + 45 + 4)
            self.assertEqual(len(manifest['retained_legacy']), 2)
            self.assertEqual(manifest['unpublished_pairs'], 49)
            self.assertFalse(manifest['automatic_load'])
            base = 'camayoc-v0.1.0/namespaces/intigraph/bundle/'
            for row in [*manifest['files'], manifest['aliases']]:
                content = archive.extractfile(base+row['path']).read()
                self.assertEqual(hashlib.sha256(content).hexdigest(), row['sha256'])

    def test_same_revision_repeats_identical_bytes(self):
        one = PACK.package("HEAD", "0.1.0", self.root / "one")
        two = PACK.package("HEAD", "0.1.0", self.root / "two")
        self.assertEqual(one, two)
        self.assertEqual((self.root / "one" / one["artifact"]).read_bytes(), (self.root / "two" / two["artifact"]).read_bytes())

    def test_archive_envelope_is_platform_neutral(self):
        proof = PACK.package("HEAD", "0.1.0", self.root / "neutral")
        data = (self.root / "neutral" / proof["artifact"]).read_bytes()
        self.assertEqual(data[9], 255, "gzip OS must be unknown on every build host")
        self.assertEqual(data[4:8], b"\0" * 4, "gzip timestamp must be zero")
        self.assertEqual(data[3] & 8, 0, "gzip must not embed an output filename")
        expected = self.git("archive", "--format=tar", "--prefix=camayoc-v0.1.0/", "HEAD")
        self.assertEqual(gzip.decompress(data), expected)

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
