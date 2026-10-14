"""Native offline staging controls; no server or hook activation.

# arming: library owned temporary databases and explicit native CLI only
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("loader", ROOT / "scripts/governance_loader.py")
loader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loader)


class NativeLoaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source_temp = tempfile.TemporaryDirectory(prefix="governance-producer-")
        cls.addClassCleanup(cls.source_temp.cleanup)
        root = Path(cls.source_temp.name)
        binary = os.environ.get("QUIPU_BIN") or shutil.which("quipu")
        if not binary:
            raise RuntimeError("native Quipu is required; no skip/green substitute")
        catalog = ROOT / "governance/catalog.ttl"
        profile = ROOT / "governance/shapes.ttl"
        producer = root / "producer.db"
        artifact = root / "resource.pendant.db"
        loader.native(binary, "knot", str(catalog), "--db", str(producer))
        loader.native(binary, "shapes", "load", "quechua-governance", str(profile), "--db", str(producer))
        loader.native(binary, "pack", "urn:quipu:graph:root", "--out", str(artifact),
                      "--name", "quechua-governance", "--version", "0.7.0",
                      "--shapes", "quechua-governance", "--destination", "internal", "--db", str(producer))
        cls.fixture_options = dict(artifact=artifact,
            file_sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(),
            content_hash="sha256:e3952ce1919300af934e0a2846836e559d3d900652ecbbf7cbbd9889ba056e17",
            version="0.7.0", graph="urn:camayoc:test:governance", shape="quechua-governance",
            catalog_path=catalog, profile_path=profile,
            catalog_sha256=hashlib.sha256(catalog.read_bytes()).hexdigest(),
            profile_sha256=hashlib.sha256(profile.read_bytes()).hexdigest(), binary=binary)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="governance-controls-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.options = self.fixture_options | {"output": self.root / "receiver.db"}

    def test_full_stage_and_verified_repeat(self):
        first = loader.stage(**self.options)
        self.assertEqual(first["direct_declarations"], 449)
        self.assertEqual((first["catalog_triples"], first["profile_triples"]), (1418, 2133))
        before = self.options["output"].read_bytes()
        self.assertEqual(loader.stage(**self.options)["outcome"], "verified-existing")
        self.assertEqual(before, self.options["output"].read_bytes())

    def test_identity_and_destination_refusals_leave_no_output(self):
        for changes in ({"file_sha256": "0" * 64}, {"version": "0.8.0"},
                        {"content_hash": "sha256:" + "0" * 64}, {"shape": "other"},
                        {"graph": "urn:quipu:graph:root"}, {"graph": "relative"},
                        {"catalog_sha256": "0" * 64}, {"profile_sha256": "0" * 64},
                        {"artifact": self.root / "missing"}):
            with self.subTest(changes=changes), self.assertRaises(loader.Refused):
                loader.stage(**(self.options | changes))
            self.assertFalse(self.options["output"].exists())

    def test_changed_catalog_with_pinned_bytes_still_refuses_native_readback(self):
        wrong = self.root / "wrong.ttl"
        wrong.write_bytes(self.options["catalog_path"].read_bytes() +
                          b'\n<urn:extra> <urn:predicate> "foreign" .\n')
        with self.assertRaises(loader.Refused):
            loader.stage(**(self.options | {"catalog_path": wrong,
                "catalog_sha256": hashlib.sha256(wrong.read_bytes()).hexdigest()}))
        self.assertFalse(self.options["output"].exists())

    def test_existing_foreign_store_is_preserved(self):
        output = self.options["output"]
        output.write_bytes(b"unrelated bytes")
        output.chmod(0o600)
        with self.assertRaises(loader.Refused):
            loader.stage(**self.options)
        self.assertEqual(output.read_bytes(), b"unrelated bytes")

    def test_symlink_and_input_destination_refused(self):
        output = self.options["output"]
        original = self.options["artifact"].read_bytes()
        output.symlink_to(self.options["artifact"])
        with self.assertRaises(loader.Refused):
            loader.stage(**self.options)
        self.assertEqual(original, self.options["artifact"].read_bytes())
        with self.assertRaises(loader.Refused):
            loader.stage(**(self.options | {"output": self.options["artifact"]}))

    def test_isolated_server_readback_and_atomic_refusals(self):
        server = os.environ.get("QUIPU_SERVER_BIN")
        self.assertTrue(server, "an explicitly selected server binary is required")
        loader.stage(**self.options)
        config = self.root / ".bobbin/config.toml"
        config.parent.mkdir()
        config.write_text('[quipu.shacl]\nvalidate_on_write = true\n')
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = {k: v for k, v in os.environ.items() if not k.startswith("QUIPU_")}
        process = subprocess.Popen([server, "--db", str(self.options["output"]),
                                    "--bind", f"127.0.0.1:{port}"],
                                   cwd=self.root, env=env,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        def request(path, body=None):
            req = urllib.request.Request(f"http://127.0.0.1:{port}" + path,
                data=None if body is None else json.dumps(body).encode(),
                headers={"Content-Type": "application/json", "X-Quipu-Client": "agent-adhoc"})
            try:
                with urllib.request.urlopen(req, timeout=3) as response:
                    raw = response.read(1_000_001)
                    self.assertLessEqual(len(raw), 1_000_000, "truncated response is UNKNOWN")
                    return json.loads(raw)
            except urllib.error.HTTPError as error:
                error.add_note(error.read(4096).decode(errors="replace"))
                raise

        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                try:
                    request("/health")
                    break
                except (OSError, urllib.error.URLError):
                    if process.poll() is not None:
                        self.fail("isolated server exited before health control")
                    time.sleep(0.1)
            else:
                self.fail("isolated server health UNKNOWN")
            q = "https://scbrown.github.io/quechua/ns#"
            declarations = request("/query", {"query":
                f'SELECT ?s ?t WHERE {{ GRAPH <{self.options["graph"]}> {{ ?s a ?t . '
                'FILTER(?t = <http://www.w3.org/2000/01/rdf-schema#Class> || '
                '?t = <http://www.w3.org/1999/02/22-rdf-syntax-ns#Property>) } } LIMIT 1000'})
            self.assertFalse(declarations.get("truncated", True))
            self.assertEqual(declarations["count"], 449)
            expected = {(str(s), str(t)) for s, predicate, t in
                        loader.Graph().parse(self.options["catalog_path"], format="turtle")
                        if predicate == loader.RDF.type and t in (loader.RDF.Property, loader.RDFS.Class)}
            self.assertEqual({(row["s"], row["t"]) for row in declarations["rows"]}, expected)
            profile = request("/shapes", {"action": "get", "name": self.options["shape"]})
            self.assertTrue(loader.isomorphic(loader.Graph().parse(data=profile["turtle"], format="turtle"),
                              loader.Graph().parse(self.options["profile_path"], format="turtle")))
            valid = (f'<urn:test:policy> a <{q}AdmissionPolicy> ; <{q}claim> "example" ; '
                     f'<{q}targets> "example/**" ; <{q}status> "proposed" .')
            unknown = '<urn:test:unknown> a <urn:test:UnknownClass> .'
            invalid = valid.replace('urn:test:policy', 'urn:test:invalid').replace('"proposed"', '"active"')
            with self.assertRaises(urllib.error.HTTPError) as refused:
                request("/knot", {"turtle": valid + unknown, "actor": "library-test", "source": "governance-test"})
            self.assertEqual(refused.exception.code, 400)
            for name in ["policy", "unknown"]:
                self.assertFalse(request("/query", {"query": f'ASK {{ <urn:test:{name}> ?p ?o }}'})["result"])
            # Native SHACL refusals are structured HTTP 200 feedback. Status
            # alone is not acceptance; conformance and actual absence govern.
            feedback = request("/knot", {"turtle": valid + invalid,
                "actor": "library-test", "source": "governance-test"})
            self.assertIs(feedback["conforms"], False)
            self.assertGreater(feedback["violations"], 0)
            status_issues = [issue for issue in feedback["issues"]
                             if issue["focus_node"] == "urn:test:invalid"
                             and issue["path"] == q + "status"]
            self.assertTrue(status_issues)
            self.assertTrue(all(issue["severity"] and issue["message"] for issue in status_issues))
            for name in ["policy", "invalid"]:
                self.assertFalse(request("/query", {"query": f'ASK {{ <urn:test:{name}> ?p ?o }}'})["result"])
            # A real positive instance proves the typed writer and shape gate
            # were exercised; it is proposed, not an activated policy.
            request("/knot", {"turtle": valid, "actor": "library-test", "source": "governance-test"})
            self.assertTrue(request("/query", {"query": f'ASK {{ <urn:test:policy> a <{q}AdmissionPolicy> }}'})["result"])
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
