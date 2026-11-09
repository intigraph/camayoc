#!/usr/bin/env python3
"""Packaging/readback refusal controls; synthetic data is not native acceptance.

# arming: ci; release asset publication contract invokes this script.
"""

import importlib.util
import json
import os
import subprocess
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


publisher = load("publisher_test", ROOT / "publish_repository_share.py")
verifier = load("verifier_test", ROOT / "verify-repository-share.py")


class Publication(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {"GITHUB_REPOSITORY": "intigraph/camayoc"})
        environment.start()
        self.addCleanup(environment.stop)
        identity_patch = patch.object(
            verifier.subprocess, "run",
            return_value=SimpleNamespace(stdout=json.dumps({"id": 1324782492, "full_name": "intigraph/camayoc"})),
        )
        self.identity_api = identity_patch.start()
        self.addCleanup(identity_patch.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stack_map_path = self.root / "stack-owner-map.json"
        self.stack_map_path.write_text(json.dumps({
            "legacy_owner": "scbrown", "target_owner": "intigraph",
            "repositories": {"camayoc": "intigraph"},
        }))
        map_environment = patch.dict(os.environ, {"REPOSITORY_SHARE_STACK_MAP": str(self.stack_map_path)})
        map_environment.start()
        self.addCleanup(map_environment.stop)
        self.stage = self.root / "stage"
        self.stage.mkdir()
        recipe = {"model": "model.onnx", "model_sha256": "a" * 64, "tokenizer_sha256": "b" * 64, "dimension": 384}
        (self.stage / "manifest.json").write_text(json.dumps({"embedding": recipe}))
        for name in ("export.nt", "manifest.ttl", "shapes.ttl", "queries.ttl"):
            (self.stage / name).write_text("# synthetic fixture only\n")
        self.archive = self.root / "camayoc-v1.2.3-repository.pendant.tar.gz"
        self.revision = "1" * 40

    def prepare(self):
        publisher.package(self.stage, self.archive)
        sha = publisher.digest(self.archive)
        self.archive.with_name(self.archive.name + ".sha256").write_text(sha + "  " + self.archive.name + "\n")
        self.proof = {
            "schema": "quipu.repository-share-producer/v1",
            "source_repository": "intigraph/camayoc",
            "source_revision": self.revision,
            "release_tag": "v1.2.3",
            "archive_sha256": sha,
            "destination": "outward",
            "quipu_sha256": "2" * 64,
            "quipu_server_sha256": "5" * 64,
            "bobbin_sha256": "3" * 64,
            "engine_source_revision": "4" * 40,
        }
        self.proof_path = self.archive.with_name(self.archive.name + ".provenance.json")
        self.proof_path.write_text(json.dumps(self.proof))

    def test_deterministic_archive_and_exact_readback(self):
        self.prepare()
        other = self.root / "second.tar.gz"
        publisher.package(self.stage, other)
        self.assertEqual(self.archive.read_bytes(), other.read_bytes())
        self.assertEqual(verifier.verify(self.root, "v1.2.3", self.revision)["source_revision"], self.revision)

    def test_required_members_and_recipe(self):
        (self.stage / "queries.ttl").unlink()
        with self.assertRaisesRegex(ValueError, "required nonempty"):
            publisher.check_share(self.stage)
        (self.stage / "queries.ttl").write_text("# query")
        (self.stage / "manifest.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "provider-byte"):
            publisher.check_share(self.stage)

    def test_text_line_span_and_private_members_refuse(self):
        for predicate in ("content", "startLine", "endLine"):
            (self.stage / "export.nt").write_text(
                "<https://example.org/s> <http://aegis.gastown.local/ontology/" + predicate + '> "body" .\n'
            )
            with self.subTest(predicate=predicate), self.assertRaisesRegex(ValueError, "duplicates source"):
                publisher.check_share(self.stage)
        (self.stage / "export.nt").write_text("# empty synthetic")
        (self.stage / "policy-token").write_text("not a real token")
        with self.assertRaisesRegex(ValueError, "unexpected"):
            publisher.check_share(self.stage)

    def test_foreign_repository_and_wrong_source_refuse(self):
        self.prepare()
        for field, value in [
            ("source_repository", "scbrown/quipu"),
            ("source_revision", "f" * 40),
            ("release_tag", "v9.9.9"),
            ("archive_sha256", "f" * 64),
        ]:
            self.proof_path.write_text(json.dumps({**self.proof, field: value}))
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "provenance mismatch"):
                verifier.verify(self.root, "v1.2.3", self.revision)

    def test_checksum_refuses(self):
        self.prepare()
        self.archive.with_name(self.archive.name + ".sha256").write_text("wrong")
        with self.assertRaisesRegex(ValueError, "checksum"):
            verifier.verify(self.root, "v1.2.3", self.revision)

    def test_hold_absence_presence_symlink_and_unknown(self):
        path = self.root / "operator-hold"
        with patch.dict(os.environ, {"QUIPU_SHARE_HOLD_FILE": str(path)}):
            self.assertFalse(publisher.held())
            path.write_text("hold")
            self.assertTrue(publisher.held())
            path.unlink()
            path.symlink_to(self.root / "missing")
            self.assertTrue(publisher.held())
            with patch.object(Path, "lstat", side_effect=PermissionError("fixture")):
                with self.assertRaisesRegex(ValueError, "hold state unreadable"):
                    publisher.held()

    def test_repository_context_and_origin_validation(self):
        for identity in ("intigraph/camayoc", "another-owner/renamed-repo"):
            with self.subTest(identity=identity), patch.dict(os.environ, {"GITHUB_REPOSITORY": identity}):
                self.assertEqual(publisher.repository_identity(), identity)
        for identity in ("", "../camayoc", "owner/repo/extra", "https://github.com/owner/repo", "owner/repo\n"):
            with self.subTest(identity=identity), patch.dict(os.environ, {"GITHUB_REPOSITORY": identity}):
                with self.assertRaisesRegex(ValueError, "invalid GITHUB_REPOSITORY"):
                    publisher.repository_identity()
        with patch.dict(os.environ):
            os.environ.pop("GITHUB_REPOSITORY", None)
            for remote in ("https://github.com/intigraph/camayoc.git", "git@github.com:intigraph/camayoc.git"):
                with patch("subprocess.check_output", return_value=remote):
                    self.assertEqual(publisher.repository_identity(), "intigraph/camayoc")
            with patch("subprocess.check_output", return_value="https://example.org/intigraph/camayoc.git"):
                with self.assertRaisesRegex(ValueError, "origin must identify"):
                    publisher.repository_identity()

    def test_exact_repository_retains_literal_offline_contract(self):
        self.prepare()
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": "another-owner/renamed-repo"}):
            with self.assertRaisesRegex(ValueError, "provenance mismatch"):
                verifier.verify(self.root, "v1.2.3", self.revision)
            self.proof_path.write_text(json.dumps({**self.proof, "source_repository": "another-owner/renamed-repo"}))
            self.assertEqual(verifier.verify(self.root, "v1.2.3", self.revision)["source_repository"],
                             "another-owner/renamed-repo")
        self.identity_api.assert_not_called()

    def test_undeclared_alias_same_id_is_refused(self):
        self.prepare()
        self.proof_path.write_text(json.dumps({**self.proof, "source_repository": "scbrown/camayoc"}))
        with patch.dict(os.environ, {"REPOSITORY_SHARE_STACK_MAP": str(self.root / "absent-map.json")}):
            with self.assertRaisesRegex(ValueError, "map|declared"):
                verifier.verify(self.root, "v1.2.3", self.revision)
        self.identity_api.assert_not_called()

    def test_declared_alias_receipt_requires_same_numeric_repository(self):
        self.prepare()
        self.proof_path.write_text(json.dumps({**self.proof, "source_repository": "scbrown/camayoc"}))
        self.assertEqual(verifier.verify(self.root, "v1.2.3", self.revision)["source_repository"],
                         "intigraph/camayoc")
        self.assertEqual({call.args[0][2] for call in self.identity_api.call_args_list},
                         {"repos/scbrown/camayoc", "repos/intigraph/camayoc"})

    def test_wrong_id_or_canonical_name_refuses_alias(self):
        self.prepare()
        self.proof_path.write_text(json.dumps({**self.proof, "source_repository": "scbrown/camayoc"}))
        for identity in (
            {"id": 1324782493, "full_name": "intigraph/camayoc"},
            {"id": "1324782492", "full_name": "intigraph/camayoc"},
            {"id": True, "full_name": "intigraph/camayoc"},
            {"id": 1324782492, "full_name": "other/camayoc"},
            {"id": 1324782492, "full_name": []}, {}, [],
        ):
            with self.subTest(identity=identity):
                self.identity_api.return_value.stdout = json.dumps(identity)
                with self.assertRaisesRegex(ValueError, "provenance mismatch"):
                    verifier.verify(self.root, "v1.2.3", self.revision)

    def test_both_alias_lookups_must_succeed(self):
        self.prepare()
        self.proof_path.write_text(json.dumps({**self.proof, "source_repository": "scbrown/camayoc"}))
        good = SimpleNamespace(stdout=json.dumps({"id": 1324782492, "full_name": "intigraph/camayoc"}))
        for bad in (subprocess.TimeoutExpired("gh", 20),
                    subprocess.CalledProcessError(1, "gh"), OSError("fixture unavailable")):
            with self.subTest(error=type(bad).__name__):
                self.identity_api.side_effect = [good, bad]
                with self.assertRaisesRegex(ValueError, "lookup unavailable"):
                    verifier.verify(self.root, "v1.2.3", self.revision)
        self.identity_api.side_effect = None

    def test_malformed_identity_response_refuses(self):
        self.prepare()
        self.proof_path.write_text(json.dumps({**self.proof, "source_repository": "scbrown/camayoc"}))
        for response in ("", "not json", "x" * 65537):
            with self.subTest(response_length=len(response)):
                self.identity_api.return_value.stdout = response
                with self.assertRaisesRegex(ValueError, "lookup unavailable"):
                    verifier.verify(self.root, "v1.2.3", self.revision)

    def test_undeclared_delivered_alias_refuses_before_api(self):
        self.prepare()
        for name in ("another-owner/camayoc", "intigraph/quipu", "scbrown/camayoc-extra", None, []):
            with self.subTest(name=name):
                self.proof_path.write_text(json.dumps({**self.proof, "source_repository": name}))
                self.identity_api.reset_mock()
                with self.assertRaisesRegex(ValueError, "provenance mismatch"):
                    verifier.verify(self.root, "v1.2.3", self.revision)
                self.identity_api.assert_not_called()

    def test_map_must_explicitly_declare_both_aliases(self):
        self.prepare()
        self.proof_path.write_text(json.dumps({**self.proof, "source_repository": "scbrown/camayoc"}))
        for data in ({}, {"legacy_owner": "scbrown", "target_owner": "intigraph", "repositories": {}},
                     {"legacy_owner": "scbrown", "target_owner": "other", "repositories": {"camayoc": "other"}},
                     {"legacy_owner": "scbrown", "target_owner": "intigraph", "repositories": {"camayoc": []}}):
            with self.subTest(data=data):
                self.stack_map_path.write_text(json.dumps(data))
                self.identity_api.reset_mock()
                with self.assertRaisesRegex(ValueError, "map|declared"):
                    verifier.verify(self.root, "v1.2.3", self.revision)
                self.identity_api.assert_not_called()

    def test_alias_id_mismatch_refuses(self):
        self.prepare()
        self.proof_path.write_text(json.dumps({**self.proof, "source_repository": "scbrown/camayoc"}))
        self.identity_api.side_effect = [SimpleNamespace(stdout=json.dumps({"id": 1324782492, "full_name": "intigraph/camayoc"})),
                                         SimpleNamespace(stdout=json.dumps({"id": 99, "full_name": "intigraph/camayoc"}))]
        with self.assertRaisesRegex(ValueError, "provenance mismatch"):
            verifier.verify(self.root, "v1.2.3", self.revision)

    def test_wait_is_bounded(self):
        calls, pauses = [], []

        class Result:
            returncode = 1

        def run(args, **kwargs):
            calls.append(args)
            return Result()

        with self.assertRaisesRegex(ValueError, "bounded"):
            verifier.wait("v1.2.3", self.revision, attempts=2, pause=pauses.append, run=run)
        self.assertTrue(all(args[args.index("--repo") + 1] == "intigraph/camayoc" for args in calls))
        self.assertEqual(len(calls), 2)
        self.assertEqual(pauses, [30])


def load_tests(loader, tests, pattern):
    provider_tests = load("repository_embedding_controls", ROOT / "test-repository-share-embedding.py")
    tests.addTests(loader.loadTestsFromModule(provider_tests))
    return tests


if __name__ == "__main__":
    unittest.main()
