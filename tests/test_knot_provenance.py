"""Every knot write names its actor and its source — camayoc-99t.

Ingress rule 1 used to say "every camayoc ingress goes through `/episode` … no
free-Turtle writes to governed planes". That was false in every particular: the
only `/episode` calls in the repository are the four deliberately-invalid gate
probes, and every successful ingest has always been `POST /knot`.

The rule is now narrowed to what camayoc actually does — episode-shaped writes
for agent-recorded facts, knot writes carrying `actor` and `source` for bulk
deterministic loads. A narrowed rule is only worth the narrowing if something
holds it to its new terms, otherwise "we write to /knot with provenance"
degrades into "we write to /knot" the first time somebody adds a script.

So this suite is the enforcement half. `actor` + `source` is a weaker record
than a PROV activity — it names who wrote and what they read, but not the run,
and it does not chain. Weaker is the accepted trade. Absent is not.

The seed arm is behavioural: it runs the real script against a stub store and
reads the payload that arrives. The others are static, because bootstrap needs
a whole install path and reconcile needs a Prometheus catalogue, and a static
check that is honest about being static beats an integration test nobody can
run. Both kinds fail on the thing that actually regresses: a new knot write
that forgot its provenance.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"

#: Provenance every knot write must carry under the narrowed rule 1.
REQUIRED = ("actor", "source")

#: A script posts to an endpoint when it builds a curl target for it. Matching
#: the bare path instead would count prose: seed_knowledge.sh discusses
#: "POST /episode" in a comment explaining why it does not use it.
POSTS_KNOT = re.compile(r"\$SERVER/knot")
POSTS_EPISODE = re.compile(r"\$SERVER/episode")


class KnotWriteInventoryTests(unittest.TestCase):
    """Static: find the knot writes, insist each one builds provenance.

    Deliberately driven off a search rather than a hand-written list. A list
    would stay green when somebody adds the script it does not know about,
    which is the only failure mode worth defending against here.
    """

    def knot_writers(self) -> list[Path]:
        found = [p for p in sorted(SCRIPTS.glob("*.sh")) if POSTS_KNOT.search(p.read_text())]
        # If this ever finds nothing, the search broke — not the provenance.
        self.assertTrue(found, "no script posts to /knot; the search is wrong")
        return found

    def test_every_knot_writer_names_its_actor_and_source(self):
        for path in self.knot_writers():
            text = path.read_text()
            # Only the payload-building code matters. Prose in a comment that
            # happens to say "source" must not satisfy this, so match the JSON
            # key form with its quotes and colon.
            #
            # Coarse on purpose, and worth naming: this is per FILE, not per
            # call site, so a script with two knot writes passes when only one
            # carries provenance. Tightening it means parsing shell, and the
            # regression it would catch (a second knot write added to an
            # existing script) is rarer than the one it does catch (a new
            # script with none). The seed arm below is per-payload and exact.
            for key in REQUIRED:
                with self.subTest(script=path.name, key=key):
                    self.assertRegex(
                        text,
                        rf'"{key}"\s*:',
                        f'{path.name} posts to /knot without building a "{key}" key. '
                        "Ingress rule 1 narrowed to knot writes that carry both; a "
                        "write that carries neither is the thing the narrowing was "
                        "not allowed to become.",
                    )

    def test_no_knot_payload_carries_a_graph_key(self):
        """The trap camayoc-s0h would otherwise walk into.

        `/knot` writes to ROOT unconditionally (quipu `src/rdf.rs:180-182`) and
        parses its body as free-form JSON with no unknown-field rejection
        (`src/mcp/mod.rs:411-436`). So a `"graph"` key added here to "implement
        routing" is accepted, ignored, and produces a green run that routes
        nothing — the failure would be invisible until somebody queried a plane
        and found it empty.

        Routing belongs on `/episode`, which does have a `graph` field
        (`src/episode/mod.rs:177-181`). When that move happens this test should
        fail on the script that moved, and the fix is to move it out of the knot
        inventory — not to loosen this.
        """
        for path in self.knot_writers():
            with self.subTest(script=path.name):
                self.assertNotRegex(
                    path.read_text(),
                    r'"graph"\s*:',
                    f"{path.name} builds a \"graph\" key on a /knot payload, which quipu "
                    "silently drops. Route through /episode instead.",
                )

    def test_the_only_episode_writes_are_the_gate_probes(self):
        """The premise the narrowing rests on, pinned.

        If real ingest ever does move to `/episode`, this fails — and the right
        response is to widen rule 1 back, not to delete the test.
        """
        writers = {
            p.name for p in sorted(SCRIPTS.glob("*.sh")) if POSTS_EPISODE.search(p.read_text())
        }
        self.assertEqual({"gate_probe.sh"}, writers)

    def test_prov_wasgeneratedby_is_still_absent_from_the_code(self):
        """Rule 1 no longer claims quipu supplies it on the paths we use.

        Should a knot write start carrying a PROV activity, this fails and the
        doc's "costs something" paragraph needs revisiting.
        """
        # READERS of the PROV edge quipu's own /episode writes are not knot
        # writers. Each exemption names why; it must never write the edge.
        readers = {"directive_edges.py": "anchors a Directive's grace window on "
                   "its capture episode (aegis-q9m5mp.47); writes only governedBy, "
                   "trackedBy and inferredAt",
                   "exact_work_item_coverage.py": "reads the positive control's "
                   "capture episode in exact-subject snapshots; its transport "
                   "permits only /query and never writes the edge"}
        for path in sorted(SCRIPTS.rglob("*")):
            if path.suffix in (".sh", ".py") and path.is_file() and path.name not in readers:
                self.assertNotIn("wasGeneratedBy", path.read_text(), str(path))


class SeedKnotPayloadTests(unittest.TestCase):
    """Behavioural: run the seed, read what actually lands on the wire."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.src = Path(self._tmp.name) / "src"
        self.src.mkdir()
        (self.src / "widget.py").write_text('"""A widget."""\n\n\ndef spin():\n    return 1\n')

    def test_the_seed_sends_actor_and_source_in_its_knot_payload(self):
        captured: list[dict] = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                if self.path.rstrip("/").endswith("knot"):
                    captured.append(json.loads(raw))
                body = json.dumps({"outcome": "created", "count": 1, "conforms": True}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            result = subprocess.run(
                ["bash", str(SCRIPTS / "seed_knowledge.sh"), str(self.src)],
                env={
                    "PATH": "/usr/bin:/bin:/usr/local/bin",
                    "QUIPU_SERVER": f"http://127.0.0.1:{server.server_port}",
                    "CLAUDE_PLUGIN_ROOT": str(ROOT),
                    "CLAUDE_PROJECT_DIR": self._tmp.name,
                },
                capture_output=True,
                text=True,
                timeout=180,
            )
        finally:
            server.shutdown()
            server.server_close()

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(1, len(captured), "expected exactly one knot write")
        payload = captured[0]
        for key in REQUIRED:
            self.assertIn(key, payload)
            self.assertTrue(str(payload[key]).strip(), f"{key} is present but empty")
        self.assertEqual(str(self.src), payload["source"])


class IngressDocTests(unittest.TestCase):
    """The doc must not re-grow the claim camayoc-99t disproved."""

    def test_rule_one_does_not_claim_episode_only_ingress(self):
        rule_section = (ROOT / "docs" / "design" / "ingress.md").read_text()
        head = rule_section.split("## 2.")[0]
        # Only the LIVE rule is under test. The paragraph beginning "This rule
        # previously read" quotes the disproved wording on purpose, and a test
        # that forbade the quotation would forbid recording the correction.
        live = head.split("This rule previously read")[0]
        self.assertIn("This rule previously read", head, "the correction was dropped")
        self.assertNotRegex(live, r"[Ee]very camayoc ingress goes through")
        self.assertNotRegex(live, r"No free-Turtle writes to governed planes")
        # The narrowing has to name the endpoint it narrowed to, or a reader
        # cannot tell what the rule now governs.
        self.assertIn("/knot", live)
        self.assertTrue(re.search(r"`actor`\s*\+?\s*(and|\+)?\s*`?source`?", live))


if __name__ == "__main__":
    unittest.main()
