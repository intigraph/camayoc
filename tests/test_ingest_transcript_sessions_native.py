"""Actual Quipu wire-format control; private disposable server only."""
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch
import urllib.request
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from quipu_bin_guard import QUIPU_SERVER, requires_quipu_server
spec = importlib.util.spec_from_file_location("transcript_native", ROOT / "scripts/ingest_transcript_sessions.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


@requires_quipu_server
class NativeWireRecovery(unittest.TestCase):
    def test_landed_snapshot_compaction_and_string_projection(self):
        record = dict(session="00000000-0000-0000-0000-000000000001", agent="example",
                      harness="claude", at="2026-10-15T00:00:00Z", key="garage-hla:hla/agent-transcripts/example/fixture.md",
                      sha256="a" * 64, bytes=123, tasks=["example-task"])
        with TemporaryDirectory() as tmp:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            url = f"http://127.0.0.1:{port}"
            env = dict(os.environ, HOME=tmp, QUIPU_AUTH_TOKEN="isolated-test-fixture")
            env.pop("QUIPU_AUTH_TOKEN_FILE", None)
            process = subprocess.Popen([QUIPU_SERVER, "--db", str(Path(tmp)/"store.db"),
                                        "--bind", f"127.0.0.1:{port}"], cwd=tmp, env=env,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                for _ in range(80):
                    try:
                        with urllib.request.urlopen(url+"/health",timeout=1):
                            break
                    except OSError:
                        time.sleep(.05)
                else:
                    self.fail("private Quipu health never became ready")
                def post(path, body, **kwargs):
                    req = urllib.request.Request(url+path, data=json.dumps(body,sort_keys=True).encode(),
                        headers={"Content-Type":"application/json", "X-Quipu-Client":"camayoc-transcript-fixture",
                                 "Authorization":"Bearer isolated-test-fixture"})
                    try:
                        with urllib.request.urlopen(req, timeout=10) as response:
                            return json.load(response)
                    except urllib.error.HTTPError as error:
                        raise AssertionError(f"private fixture HTTP {error.code}: {error.read().decode()}") from error
                with patch.object(m.planes,"_post",side_effect=post), patch.object(m.planes,"plane_for",return_value="urn:fixture:records"):
                    created=post("/graph/create",{"graph":"urn:fixture:records"})
                    self.assertTrue(created.get("created"),created)
                    post("/shapes", {"action":"load", "name":"fixture-prefix",
                        "turtle":f"@prefix aegis: <{m.ONTOLOGY}> . @prefix sh: <http://www.w3.org/ns/shacl#> . aegis:FixtureShape a sh:NodeShape ."})
                    payload = m.body(record,"fixture")
                    response = post("/knot",payload)
                    self.assertTrue(response.get("tx_id"),response)
                    self.assertEqual(m.snapshot_outcome(record),"landed")
                    # The same real graph, without STR, exposes compact predicates.
                    _, artifact = m.iris(record)
                    raw=post("/query",{"graph":"urn:fixture:records",
                        "query":f"SELECT ?p ?o WHERE {{ <{artifact}> ?p ?o }}"})["rows"]
                    self.assertTrue(any(str(r["p"]).startswith("aegis:") for r in raw),raw)
                    # Changed declared task set must still refuse, not normalize away.
                    self.assertEqual(m.snapshot_outcome(dict(record,tasks=[])),"superseded")
            finally:
                process.terminate()
                process.wait(timeout=10)
