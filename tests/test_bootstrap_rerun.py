"""bootstrap.sh must succeed when run a second time against the same store.

caboodle's resumed install re-runs bootstrap as camayoc's functional
verification. On the first run quipu has no shapes loaded, so its vocabulary
gate is inactive and the ontology load succeeds; bootstrap then loads shapes,
and every later load of core.ttl is refused with HTTP 400 ("unknown rdf:type
IRIs: rdf:Property, rdfs:Class"). Before this test that refusal failed every
resumed install, printing an empty "ontology load FAILED:".

The second arm is what stops the first from passing vacuously: against a store
that does NOT hold the ontology, the presence check must say so.
"""
import json
import hashlib
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from quipu_bin_guard import QUIPU_SERVER as QUIPU_SERVER_BIN, requires_quipu_server  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "scripts" / "bootstrap.sh"
PRESENT = ROOT / "scripts" / "ontology_present.py"
ONTOLOGY = ROOT / "ontology" / "core.ttl"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@requires_quipu_server
class BootstrapRerunTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)
        self.server = f"http://127.0.0.1:{free_port()}"
        bindir = str(Path(QUIPU_SERVER_BIN).resolve().parent)
        self.env = dict(
            os.environ,
            PATH=bindir + os.pathsep + os.environ.get("PATH", ""),
            QUIPU_SERVER=self.server,
            CLAUDE_PROJECT_DIR=str(self.project),
            CLAUDE_PLUGIN_ROOT=str(ROOT),
            HOME=str(self.project),
        )
        self.env["QUIPU_AUTH_TOKEN"] = "isolated-test-fixture"

    def tearDown(self):
        pid = self.project / ".quipu" / "server.pid"
        if pid.exists():
            try:
                os.kill(int(pid.read_text().strip()), 15)
            except (ProcessLookupError, ValueError):
                pass
        self.temp.cleanup()

    def bootstrap(self):
        return subprocess.run(
            ["bash", str(BOOTSTRAP)], cwd=self.project, env=self.env,
            capture_output=True, text=True, timeout=300,
        )

    def test_a_second_run_against_the_same_store_succeeds(self):
        first = self.bootstrap()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertIn("ontology: loaded (core.ttl)", first.stdout)

        second = self.bootstrap()
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertIn("server: already running", second.stdout)
        self.assertIn("ontology: already present", second.stdout)
        self.assertNotIn("FAILED", second.stdout)

    def test_fresh_user_gets_private_matching_credential_and_reruns(self):
        self.env.pop("QUIPU_AUTH_TOKEN", None)
        self.env.pop("QUIPU_AUTH_TOKEN_FILE", None)
        first = self.bootstrap()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertIn("planes: registered and labelled", first.stdout)
        self.assertIn("gate: PROVEN", first.stdout)
        credential = self.project / ".config/quipu/token"
        secret = credential.read_text().strip()
        self.assertFalse(secret in first.stdout + first.stderr, "credential leaked")
        self.assertEqual(credential.stat().st_mode & 0o777, 0o400)
        config = self.project / ".bobbin/config.toml"
        self.assertEqual(config.stat().st_mode & 0o777, 0o600)
        self.assertIn("/.bobbin/config.toml", (self.project / ".gitignore").read_text())
        before = tuple(hashlib.sha256(p.read_bytes()).hexdigest() for p in (credential, config))
        second = self.bootstrap()
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual(before, tuple(hashlib.sha256(p.read_bytes()).hexdigest() for p in (credential, config)))
        self.assertFalse(secret in second.stdout + second.stderr, "credential leaked")
        body = json.dumps({"action": "list"}).encode()
        request = urllib.request.Request(self.server + "/shapes", data=body,
                                        headers={"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=10)
        self.assertEqual(caught.exception.code, 401)
        caught.exception.close()
        request.add_header("Authorization", "Bearer " + secret)
        with urllib.request.urlopen(request, timeout=10) as response:
            self.assertEqual(response.status, 200)
            self.assertIn("camayoc-core", response.read().decode())

    def test_bootstrapped_store_accepts_tracker_and_unanchored_observation_debt(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        from ingest_work_items import BASE_NS, episode_for

        boot = self.bootstrap()
        self.assertEqual(boot.returncode, 0, boot.stdout + boot.stderr)

        def post(body, endpoint="/episode"):
            request = urllib.request.Request(
                self.server + endpoint, data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.env["QUIPU_AUTH_TOKEN"]},
            )
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    return response.status, json.load(response)
            except urllib.error.HTTPError as error:
                with error:
                    return error.code, error.read().decode()

        record = {"id": "tracker-acceptance", "title": "Tracker acceptance",
                  "created_at": "2026-09-23T00:00:00Z", "status": "open"}
        body = episode_for(record, actor="test", source="test:tracker")
        # Legacy writes are retired. Native Seeds Actions own the tracker gate.
        status, result = post(body)
        self.assertEqual(status, 400, result)
        subject = BASE_NS + body['nodes'][0]['name']
        turtle = (f'<{subject}> a <https://schema.org/Action> ; '
                  '<http://www.w3.org/2000/01/rdf-schema#label> "Tracker acceptance" ; '
                  '<https://schema.org/name> "Tracker acceptance" ; '
                  '<https://schema.org/identifier> "fixture-1" ; '
                  '<https://schema.org/version> 1 ; '
                  '<https://schema.org/dateCreated> "2026-09-23T00:00:00Z"^^<http://www.w3.org/2001/XMLSchema#dateTime> ; '
                  '<https://seeds.local/ontology/priority> 2 ; '
                  '<https://seeds.local/ontology/issueType> "task" ; '
                  '<https://seeds.local/ontology/status> "open" ; '
                  '<https://schema.org/actionStatus> <https://schema.org/PotentialActionStatus> ; '
                  '<https://scbrown.github.io/quechua/ns#sourceKind> "declared" .')
        status, result = post({'turtle': turtle, 'graph': body['graph'],
                               'actor': 'test', 'source': 'test:seed-profile'}, '/knot')
        self.assertEqual(status, 200, result)
        self.assertIs(result.get('conforms'), True, result)
        missing_date = turtle.replace(subject, subject+'-missing-date').replace(
            '<https://schema.org/dateCreated> "2026-09-23T00:00:00Z"^^<http://www.w3.org/2001/XMLSchema#dateTime> ; ', '')
        status, result = post({'turtle': missing_date, 'graph': body['graph'],
                               'actor': 'test', 'source': 'test:seed-profile-negative'}, '/knot')
        self.assertEqual(status, 200, result)
        self.assertIs(result.get('conforms'), False, result)
        status, result = post({'query': f'SELECT ?t WHERE {{ GRAPH <{body["graph"]}> {{ <{subject}-missing-date> a ?t }} }}'}, '/query')
        self.assertEqual(status, 200, result)
        self.assertEqual(result['count'], 0, result)
        body['nodes'] = body['nodes'][1:]
        status, result = post(body)
        self.assertEqual(status, 200, result)
        self.assertIn(result["outcome"], ("created", "updated"))
        observation = body["nodes"][0]["name"]
        status, result = post({"query":
            f"SELECT ?target WHERE {{ GRAPH <{body['graph']}> {{ "
            f"<{BASE_NS}{observation}> <{BASE_NS}about> ?target }} }}"}, "/query")
        self.assertEqual(status, 200, result)
        self.assertEqual(result["count"], 1, result)

        # A MISSING anchor is write-validation debt (aegis-g51c C10): the
        # Observation minCount constraints live in the emit + sh:Warning
        # CamayocObservationProvenanceDebtShape until the writer burn-down
        # (aegis-51t) restores them. So an unanchored observation is ACCEPTED,
        # not refused.
        # A new episode/name avoids idempotence masking this arm.
        unanchored = episode_for({**record, "id": "tracker-unanchored"},
                                 actor="test", source="test:tracker")
        unanchored['nodes'] = unanchored['nodes'][1:]
        observation = unanchored["nodes"][0]["name"]
        unanchored["edges"] = [edge for edge in unanchored["edges"]
                               if not (edge["source"] == observation
                                       and edge["relation"] == "about")]
        status, result = post(unanchored)
        self.assertEqual(status, 200, result)

        # What still REJECTS: a sourceKind outside sh:in ("observed") stays in
        # CamayocObservationShape. Without this arm the one above would also
        # pass with write validation switched off.
        wrong_kind = episode_for({**record, "id": "tracker-wrong-kind"},
                                 actor="test", source="test:tracker")
        wrong_kind['nodes'] = wrong_kind['nodes'][1:]
        wrong_kind["nodes"][0]["properties"]["sourceKind"] = "declared"
        status, result = post(wrong_kind)
        self.assertEqual(status, 400, result)
        self.assertIn("SHACL", result)


@requires_quipu_server
class OntologyPresenceDiscriminatesTest(unittest.TestCase):
    """Without this arm a checker that always exits 0 would pass the test above."""

    def test_a_store_without_the_ontology_reports_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            port = free_port()
            proc = subprocess.Popen(
                [QUIPU_SERVER_BIN, "--db", str(Path(tmp) / "s.db"), "--bind", f"127.0.0.1:{port}"],
                # The server's signing key follows cwd, not its database path.
                # Keep that runtime state with the disposable database.
                cwd=tmp,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            try:
                url = f"http://127.0.0.1:{port}"
                for _ in range(60):
                    try:
                        urllib.request.urlopen(url + "/health", timeout=1)
                        break
                    except OSError:
                        time.sleep(0.25)
                out = subprocess.run(
                    [sys.executable, str(PRESENT), str(ONTOLOGY), url],
                    capture_output=True, text=True, timeout=60,
                )
                self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
                self.assertRegex(out.stdout, r"^0/\d+")
                self.assertTrue((Path(tmp) / ".quipu" / "verifier.pk8").is_file(),
                                "control: the server identity belongs in its temporary cwd")
            finally:
                proc.terminate()
                proc.wait(timeout=10)

    def test_a_dead_server_is_cannot_tell_not_missing(self):
        out = subprocess.run(
            [sys.executable, str(PRESENT), str(ONTOLOGY), f"http://127.0.0.1:{free_port()}"],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)


if __name__ == "__main__":
    unittest.main()
