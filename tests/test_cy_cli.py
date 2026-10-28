"""`camayoc` / `cy`: record, recall, topics, offline queue and `cy prime`.

docs/design/agent-memory-entities.md §3 behavior table, and tambo
docs/stack/PRIME-CONTRACT.md conformance rows. The integration class drives a
throwaway local quipu-server (never a production store): it loads the
agent-memory shapes, registers the camayoc planes and records every entity
from its own sidecar example.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from quipu_bin_guard import QUIPU_SERVER as QUIPU_SERVER_BIN, requires_quipu_server  # noqa: E402

from camayoc_cli import __version__, cli, definitions, memory, planes as cy_planes  # noqa: E402

A = "http://aegis.gastown.local/ontology/"
SECRET_TAIL = "Ab3dE5gH7jK9mN2pQ4sT6vW8yZ1bC3dF5hJ7"


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def run_cy(*argv: str, env: dict | None = None) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    # Never inherit a live endpoint or bearer from the operator shell.
    safe_env = {"QUIPU_SERVER": "http://127.0.0.1:0", "QUIPU_AUTH_TOKEN": "",
                "QUIPU_AUTH_TOKEN_FILE": os.devnull}
    safe_env.update(env or {})
    with mock.patch.dict(os.environ, safe_env), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(list(argv))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 2
    return code, out.getvalue(), err.getvalue()


def example_argv(entity: str) -> list[str]:
    e = definitions.load()["entities"][entity]
    argv = ["record", entity]
    for f in e["fields"]:
        for v in memory._values(e["example"].get(f["name"])):
            argv += [f["flag"], v]
    return argv


class OfflineTests(unittest.TestCase):
    """Everything cy can decide with no store."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {"QUIPU_SERVER": f"http://127.0.0.1:{free_port()}", "XDG_STATE_HOME": self.tmp.name,
                    "QUIPU_AUTH_TOKEN_FILE": str(Path(self.tmp.name) / "no-token"), "QUIPU_AUTH_TOKEN": "",
                    "SHANTY_AGENT": "tester"}

    def tearDown(self):
        self.tmp.cleanup()

    def test_planes_match_scripts_planes(self):
        import planes as repo_planes
        for kind in ("observed", "declared", "inferred"):
            self.assertEqual(repo_planes.plane_for(kind), cy_planes.plane_for(kind))

    def test_version_matches_plugin_and_pyproject(self):
        plugin = json.loads((ROOT / ".claude-plugin/plugin.json").read_text())["version"]
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
        self.assertEqual(plugin, __version__)
        self.assertEqual(plugin, project["version"])
        self.assertEqual({"camayoc", "cy"}, set(project["scripts"]))
        self.assertEqual(project["scripts"]["camayoc"], project["scripts"]["cy"])

    def test_missing_fields_refuse_with_the_shape_messages(self):
        code, out, err = run_cy("record", "decision", "--chose", "Use seeds.", env=self.env)
        self.assertEqual(2, code)
        fields = {f["name"]: f for f in definitions.load()["entities"]["decision"]["fields"]}
        for name in ("by", "about", "topic"):
            self.assertIn(fields[name]["message"], err)
        self.assertNotIn(fields["chose"]["message"], err)
        self.assertIn("Example: cy record decision --chose", err)
        self.assertEqual("", out)
        self.assertEqual(0, memory.queue_length(self.env), "a refused record is never queued")

    def test_secret_values_are_refused_without_echo(self):
        token = "ghp_" + SECRET_TAIL
        code, _, err = run_cy("record", "reference", "--subject", "host-vati", "--topic", "pane",
                              "--locator", f"https://x.example/?{token}", env=self.env)
        self.assertEqual(2, code)
        self.assertIn("Not recorded: locator looks like a secret (github-token", err)
        self.assertNotIn(token, err)
        code, _, err = run_cy("record", "reference", "--subject", "host-vati", "--topic", "pane",
                              "--locator", "ssh vati", "--secret-ref", "sk-" + SECRET_TAIL, env=self.env)
        self.assertEqual(2, code)
        self.assertIn("secretRef must be a secret-store key name", err)

    def test_enum_and_count_rules(self):
        code, _, err = run_cy("record", "preference", "--person", "p", "--prefers", "x", "--context", "slack",
                              "--topic", "t", env=self.env)
        self.assertEqual(2, code)
        self.assertIn("--context", err)
        argv = ["record", "handoff", "--agent", "tester"] + [a for i in range(8) for a in ("--next", f"step {i}")]
        code, _, err = run_cy(*argv, env=self.env)
        self.assertEqual(2, code)
        self.assertIn("1 to 7 next steps", err)

    def test_every_shape_field_is_a_flag(self):
        parser = cli.build_parser()
        sub = next(a for a in parser._actions if a.dest == "command").choices
        record = next(a for a in sub["record"]._actions if a.dest == "entity").choices
        for name, e in definitions.load()["entities"].items():
            with self.subTest(entity=name):
                flags = {s for a in record[name]._actions for s in a.option_strings}
                self.assertTrue({f["flag"] for f in e["fields"]} <= flags)

    def test_unreachable_store_queues_and_flush_keeps_order(self):
        code, out, _ = run_cy(*example_argv("handoff"), env=self.env)
        self.assertEqual(0, code)
        self.assertIn("queued 1", out)
        code, out, _ = run_cy(*example_argv("directive"), env=self.env)
        self.assertIn("queued 2", out)
        code, _, err = run_cy("flush", env=self.env)
        self.assertEqual(1, code)
        self.assertIn("2 still queued", err)
        lines = (Path(self.tmp.name) / "camayoc/memory-queue.jsonl").read_text().splitlines()
        self.assertEqual(["handoff", "directive"], [json.loads(x)["entity"] for x in lines])

    def test_dry_run_never_queues(self):
        code, _, err = run_cy(*example_argv("decision"), "--dry-run", env=self.env)
        self.assertEqual(3, code)
        self.assertIn("unreachable", err)
        self.assertEqual(0, memory.queue_length(self.env))


BAD_VALUES = ["a b", "<x>", 'q"uote', "it's", "line\nbreak", "nul\x00byte", "tab\there", "{x}", "a|b",
              "back\\slash", "caret^", "`tick`", "http://a b/c", "urn:x y", "aegis:", "aegis:a b", "\u00e9t\u00e9",
              "x" * 5000, "-", ".", "' } ; DROP ALL ; #", 42, 3.5, True, {"a": 1}, ["one", "two"], [None], ""]


class FuzzTests(unittest.TestCase):
    """Review of #184: no caller value may crash the CLI or the MCP server."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {"QUIPU_SERVER": f"http://127.0.0.1:{free_port()}", "XDG_STATE_HOME": self.tmp.name,
                    "QUIPU_AUTH_TOKEN": "", "QUIPU_AUTH_TOKEN_FILE": str(Path(self.tmp.name) / "none"),
                    "SHANTY_AGENT": "tester"}

    def tearDown(self):
        self.tmp.cleanup()

    def test_every_field_survives_every_bad_value(self):
        from camayoc_cli import mcp
        with mock.patch.dict(os.environ, self.env):
            for name, e in definitions.load()["entities"].items():
                base = {k: v for k, v in e["example"].items()}
                for f in e["fields"]:
                    for bad in BAD_VALUES:
                        with self.subTest(entity=name, field=f["name"], value=repr(bad)[:40]):
                            out = mcp.call(e["verb"], {**base, f["name"]: bad, "dry_run": True})
                            self.assertIn(out.code, (0, 2, 3))
                            if f["kind"] == "iri" and f["name"] != "topic" and isinstance(bad, str) \
                                    and bad and bad.strip() != "" and not re.fullmatch(r"[A-Za-z0-9._~-]+", bad):
                                self.assertEqual(2, out.code, out.lines)
                                self.assertTrue(out.lines[0].startswith("Not recorded: "), out.lines)
                for r in e["recall"]:
                    for bad in BAD_VALUES:
                        args = {"key": bad} if r.get("history") else {x: bad for x in r.get("filters", [])}
                        out = mcp.call(r["verb"], args)
                        self.assertIn(out.code, (0, 2, 3))
        self.assertEqual(0, memory.queue_length(self.env), "a bad value is refused, never queued")

    def test_cli_prints_refusals_not_tracebacks(self):
        for bad in ("a b", "<x>", "line\nbreak", "' } ; DROP ALL ; #"):
            code, _, err = run_cy("record", "decision", "--chose", "x", "--by", "person-stiwi", "--about", bad,
                                  "--topic", "t", env=self.env)
            self.assertEqual(2, code)
            self.assertNotIn("Traceback", err)
            self.assertIn("Not recorded: --about", err)
            code, _, err = run_cy("recall", "decision", "--history", bad, env=self.env)
            self.assertEqual(2, code, err)
            self.assertNotIn("Traceback", err)

    def test_mcp_server_stays_up_after_bad_calls(self):
        calls = [{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "record_decision", "arguments": {
                     "chose": "x", "by": "person-stiwi", "about": "a b", "topic": "t"}}},
                 {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "recall_decision_history",
                                                                                 "arguments": {"key": "<bad>"}}},
                 {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": "not an object"},
                 ["not", "a", "request"],
                 {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "record_handoff",
                                                                                 "arguments": {"agent": {"x": 1}, "next": 7}}},
                 {"jsonrpc": "2.0", "id": 5, "method": "ping"}]
        env = {**os.environ, **self.env, "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1"}
        proc = subprocess.run([sys.executable, "-m", "camayoc_cli", "mcp"], env=env, capture_output=True, text=True,
                              input="".join(json.dumps(c) + "\n" for c in calls) + "{not json\n", timeout=30)
        replies = [json.loads(x) for x in proc.stdout.splitlines()]
        by_id = {r.get("id"): r for r in replies}
        self.assertTrue(by_id[1]["result"]["isError"])
        self.assertIn("Not recorded: --about 'a b' is not an entity IRI or local name.",
                      by_id[1]["result"]["content"][0]["text"])
        self.assertTrue(by_id[2]["result"]["isError"])
        self.assertTrue(by_id[4]["result"]["isError"])
        self.assertEqual({}, by_id[5]["result"], "the server answers a ping after every bad call")
        self.assertEqual(7, len(replies))
        self.assertNotIn("Traceback", proc.stderr)


class _Slow(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        time.sleep(3)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')

    def log_message(self, *args):
        pass


class PrimeContractTests(unittest.TestCase):
    """PRIME-CONTRACT.md conformance rows (no store file, so no 'missing store' row)."""

    def prime(self, *flags, server=None):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as cwd:
            env = {**os.environ, "HOME": home, "XDG_STATE_HOME": str(Path(home) / "state"),
                   "QUIPU_SERVER": server or f"http://127.0.0.1:{free_port()}", "PYTHONDONTWRITEBYTECODE": "1",
                   "PYTHONPATH": str(ROOT)}
            env.pop("QUIPU_AUTH_TOKEN", None)
            env.pop("QUIPU_AUTH_TOKEN_FILE", None)
            before = sorted(p for d in (home, cwd) for p in Path(d).rglob("*"))
            start = time.monotonic()
            proc = subprocess.run([sys.executable, "-m", "camayoc_cli", "prime", *flags], cwd=cwd, env=env,
                                  capture_output=True, text=True, timeout=10)
            elapsed = time.monotonic() - start
            after = sorted(p for d in (home, cwd) for p in Path(d).rglob("*"))
            self.assertEqual(before, after, "prime must create nothing under HOME or the working directory")
        return proc, elapsed

    def test_unreachable_server_prints_full_primer(self):
        proc, _ = self.prime()
        self.assertEqual(0, proc.returncode)
        self.assertIn("# Camayoc agent primer", proc.stdout)
        self.assertIn("## Next steps", proc.stdout)
        self.assertIn("unreachable", proc.stdout)
        doc = json.loads(self.prime("--json")[0].stdout)
        self.assertEqual("unreachable", doc["live"]["status"])
        self.assertEqual("degraded", doc["status"])

    def test_json_shape(self):
        doc = json.loads(self.prime("--json", "--static")[0].stdout)
        for key in ("schema", "tool", "version", "primer", "live", "status"):
            self.assertIn(key, doc)
        self.assertEqual("stack-prime/1", doc["schema"])
        self.assertEqual("camayoc", doc["tool"])
        self.assertEqual("skipped", doc["live"]["status"])
        self.assertEqual("ok", doc["status"])
        self.assertEqual(["What camayoc is", "Agent workflow", "Core commands", "MCP tools"],
                         doc["primer"]["sections"][:4])
        self.assertIn("text", doc["primer"])
        brief = json.loads(self.prime("--brief", "--json", "--static")[0].stdout)
        self.assertNotIn("text", brief["primer"])

    def test_brief_is_five_to_ten_lines(self):
        for flags in (("--brief",), ("--brief", "--static")):
            with self.subTest(flags=flags):
                proc, _ = self.prime(*flags)
                lines = proc.stdout.strip().splitlines()
                self.assertTrue(5 <= len(lines) <= 10, lines)
                self.assertTrue(lines[0].startswith(f"camayoc {__version__}: "))
                self.assertEqual("Run `cy prime` for the full primer.", lines[-1])

    def test_time_budget_against_a_slow_backend(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Slow)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            proc, _ = self.prime("--json", server=f"http://127.0.0.1:{server.server_port}")
        finally:
            server.shutdown()
            server.server_close()
        doc = json.loads(proc.stdout)
        self.assertEqual("partial", doc["live"]["status"])
        self.assertLess(doc["live"]["elapsed_ms"], 1000)

    def test_sections(self):
        proc, _ = self.prime("--section", "core")
        self.assertEqual(0, proc.returncode)
        self.assertTrue(proc.stdout.startswith("## Core commands"))
        proc, _ = self.prime("--section", "nope")
        self.assertEqual(2, proc.returncode)
        self.assertIn("What camayoc is", proc.stderr)


TEST_SHAPES = """@prefix sh: <http://www.w3.org/ns/shacl#> . @prefix aegis: <http://aegis.gastown.local/ontology/> .
aegis:TestPersonShape a sh:NodeShape ; sh:targetClass aegis:Person .
"""


@requires_quipu_server
class LocalStoreTests(unittest.TestCase):
    """The §3 behavior table against a throwaway local quipu-server."""

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.port = free_port()
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.proc = subprocess.Popen([QUIPU_SERVER_BIN, "--db", str(Path(cls.temp.name) / "store.db"),
                                     "--bind", f"127.0.0.1:{cls.port}"], cwd=cls.temp.name,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                cls.post("/health")
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("ephemeral quipu-server did not start")
        cls.post("/knot", {"turtle": (ROOT / "ontology/core.ttl").read_text(), "actor": "test"})
        cls.post("/shapes", {"action": "load", "name": "agent-memory",
                             "turtle": (ROOT / "shapes/agent-memory.shapes.ttl").read_text()})
        cls.post("/shapes", {"action": "load", "name": "test-person", "turtle": TEST_SHAPES})
        for kind in ("declared", "observed", "inferred"):
            g = cy_planes.plane_for(kind)
            cls.post("/graph/create", {"graph": g})
            cls.post("/graph/label", {"graph": g, "timestamp": "2026-01-01T00:00:00Z", "actor": "test",
                                      "trust": {"iri": g + "/trust", "chain": "urn:test:chain",
                                                "rank": {"declared": 30, "observed": 20, "inferred": 0}[kind]}})
        seed = "".join(f"<{A}{n}> <http://www.w3.org/2000/01/rdf-schema#label> \"{n}\" .\n" for n in (
            "topic-public-launch", "repo-goldblum", "system-seeds-board", "hammond", "sattler", "host-vati"))
        seed += f"<{A}person-stiwi> a <{A}Person> ; <http://www.w3.org/2000/01/rdf-schema#label> \"Stiwi\" .\n"
        seed += f"<{A}person-kai> a <{A}Person> ; <http://www.w3.org/2000/01/rdf-schema#label> \"Kai\" .\n"
        seed += f"<{A}person-robin> a <{A}Person> ; <http://www.w3.org/2000/01/rdf-schema#label> \"Robin\" .\n"
        seed += "".join(f"<{A}tie-{i:02d}> <http://www.w3.org/2000/01/rdf-schema#label> \"tie {i}\" .\n"
                        for i in range(60))
        seed += "".join(f"<{A}pager-{i:02d}> <http://www.w3.org/2000/01/rdf-schema#label> \"pager {i}\" .\n"
                        for i in range(55))
        seed += f"<{A}person-ellie> a <{A}Person> ; <http://www.w3.org/2000/01/rdf-schema#label> \"Ellie\" .\n"
        cls.post("/knot", {"turtle": seed, "actor": "test"})
        cls.state = tempfile.TemporaryDirectory()
        cls.env = {"QUIPU_SERVER": cls.base, "QUIPU_AUTH_TOKEN": "", "SHANTY_AGENT": "tester",
                   "QUIPU_AUTH_TOKEN_FILE": str(Path(cls.state.name) / "no-token"),
                   "XDG_STATE_HOME": cls.state.name, "CAMAYOC_NOW": "2026-10-28T12:00:00Z"}
        for topic in ("hostnames-in-public", "deploy-window", "chat-signoff", "board-access", "push-policy",
                      "pane-read", "history-test"):
            code, _, err = run_cy("topic", "add", topic, env=cls.env)
            assert code == 0, err

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(timeout=5)
        cls.temp.cleanup()
        cls.state.cleanup()

    @classmethod
    def post(cls, path, payload=None):
        req = urllib.request.Request(cls.base + path, data=None if payload is None else json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"},
                                     method="GET" if payload is None else "POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.load(resp)

    def cy(self, *argv, now="2026-10-28T12:00:00Z"):
        return run_cy(*argv, env={**self.env, "CAMAYOC_NOW": now})

    def test_every_entity_records_from_its_example(self):
        clock = iter(f"2026-10-28T08:00:{i:02d}Z" for i in range(10))
        for entity in sorted(definitions.load()["entities"]):
            with self.subTest(entity=entity):
                code, out, err = self.cy(*example_argv(entity), now=next(clock))
                self.assertEqual(0, code, err)
                self.assertRegex(out, rf"^(recorded {entity}|unchanged|superseded) {re.escape(A)}mem/{entity}/")
                code, out, err = self.cy(*example_argv(entity), now=next(clock))
                self.assertEqual(0, code, err)
                self.assertTrue(out.startswith("unchanged "), out)

    def test_supersession_and_history(self):
        base = ["record", "directive", "--issued-by", "person-stiwi", "--applies-to", "repo-goldblum",
                "--topic", "push-policy"]
        code, out, err = self.cy(*base, "--rule", "Open a PR for every change.", now="2026-10-01T00:00:00Z")
        self.assertEqual(0, code, err)
        old = out.split()[-1]
        code, out, err = self.cy(*base, "--rule", "Push straight to main; rebase first.", now="2026-10-02T00:00:00Z")
        self.assertEqual(0, code, err)
        self.assertTrue(out.startswith(f"superseded {old} -> "), out)
        new = out.split()[-1]
        code, out, _ = self.cy("recall", "directive", "--applies-to", "repo-goldblum", "--json")
        rows = json.loads(out)["rows"]
        self.assertEqual([new], [r["iri"] for r in rows if r["naturalKey"].endswith("/push-policy")])
        code, out, _ = self.cy("recall", "directive", "--applies-to", "crew")
        self.assertNotIn("push-policy", out, "a repo-scoped directive does not bind the crew")
        # Decision history: both versions, newest first, superseded included.
        dec = ["record", "decision", "--by", "person-stiwi", "--about", "repo-goldblum", "--topic", "history-test"]
        self.cy(*dec, "--chose", "A", now="2026-10-01T00:00:00Z")
        self.cy(*dec, "--chose", "B", now="2026-10-02T00:00:00Z")
        code, out, _ = self.cy("recall", "decision", "--history", "repo-goldblum/history-test", "--json")
        self.assertEqual(["B", "A"], [r["body"] for r in json.loads(out)["rows"]])
        code, out, _ = self.cy("recall", "decision", "--about", "repo-goldblum", "--topic", "history-test", "--json")
        self.assertEqual(["B"], [r["body"] for r in json.loads(out)["rows"]])

    def test_a_ruling_by_someone_else_needs_supersede(self):
        base = ["record", "decision", "--about", "host-vati", "--topic", "pane-read"]
        code, out, err = self.cy(*base, "--by", "person-stiwi", "--chose", "Read panes over ssh.",
                                 now="2026-10-03T00:00:00Z")
        self.assertEqual(0, code, err)
        old = out.split()[-1]
        code, out, err = self.cy(*base, "--by", "person-ellie", "--chose", "Use st agent log.",
                                 now="2026-10-04T00:00:00Z")
        self.assertEqual(2, code)
        self.assertIn(f"this replaces a ruling by person-stiwi ({old}); pass --supersede {old} to confirm.", err)
        code, out, err = self.cy(*base, "--by", "person-ellie", "--chose", "Use st agent log.",
                                 "--supersede", old, now="2026-10-04T00:00:00Z")
        self.assertEqual(0, code, err)
        self.assertTrue(out.startswith(f"superseded {old}"))

    def test_inferred_never_overrides_declared(self):
        base = ["record", "preference", "--person", "person-stiwi", "--context", "chat", "--topic", "chat-signoff"]
        code, _, err = self.cy(*base, "--prefers", "No sign-off on chat.", now="2026-10-05T00:00:00Z")
        self.assertEqual(0, code, err)
        code, _, err = self.cy(*base, "--prefers", "Sign every chat.", "--noticed", now="2026-10-06T00:00:00Z")
        self.assertEqual(2, code)
        self.assertIn("an inferred record never overrides a human-declared one", err)

    def test_decision_source_kind_follows_who_ruled(self):
        code, out, err = self.cy("record", "decision", "--by", "sattler", "--about", "system-seeds-board",
                                 "--topic", "board-access", "--chose", "Run sd locally.", "--json")
        self.assertEqual(0, code, err)
        iri = json.loads(out)["iri"]
        rows = self.post("/query", {"query": f"SELECT ?k ?g WHERE {{ GRAPH ?g {{ <{iri}> <{A}sourceKind> ?k }} }}",
                                    "verbose": True})["rows"]
        self.assertEqual("inferred", rows[0]["k"])
        self.assertEqual(cy_planes.plane_for("inferred"), rows[0]["g"])

    def test_unknown_topic_and_entity_refuse_in_plain_words(self):
        code, _, err = self.cy("record", "directive", "--rule", "x", "--issued-by", "person-stiwi",
                               "--applies-to", "crew", "--topic", "not-a-topic")
        self.assertEqual(2, code)
        self.assertIn("topic 'not-a-topic' is not registered", err)
        self.assertIn("cy topic add not-a-topic", err)
        code, _, err = self.cy("record", "directive", "--rule", "x", "--issued-by", "nobody-at-all",
                               "--applies-to", "crew", "--topic", "deploy-window")
        self.assertEqual(2, code)
        self.assertIn("--issued-by 'nobody-at-all' is not an entity in quipu", err)

    def test_the_store_refuses_in_the_same_plain_words(self):
        """Bypass cy's precheck: quipu's own SHACL refusal carries the sh:message."""
        from camayoc_cli import quipu
        turtle = (f"<{A}mem/directive/crew/x/1> a <{A}Directive> ; <{A}topic> <{A}mem/topic/deploy-window> .")
        with self.assertRaises(quipu.Refused) as ctx:
            quipu.Client(self.base).knot(turtle, cy_planes.plane_for("declared"), "test", "test")
        self.assertIn("Not recorded: a directive needs the rule as one imperative sentence (--rule).",
                      ctx.exception.messages)

    def test_handoff_past_max_age_drops_out(self):
        code, _, err = self.cy("record", "handoff", "--agent", "sattler", "--next", "Merge #182",
                               now="2026-10-20T00:00:00Z")
        self.assertEqual(0, code, err)
        code, out, _ = self.cy("recall", "handoff", "--agent", "sattler", now="2026-10-21T00:00:00Z")
        self.assertIn("Merge #182", out)
        code, out, _ = self.cy("recall", "handoff", "--agent", "sattler", now="2026-10-28T00:00:00Z")
        self.assertIn("no current handoff records match", out)

    def test_recall_uses_the_stored_query_when_loaded(self):
        manifest = json.loads((ROOT / "queries/mem_preferences.json").read_text())
        self.post("/queries", manifest)
        self.cy("record", "preference", "--person", "person-ellie", "--context", "email",
                "--topic", "chat-signoff", "--prefers", "Sign emails.")
        code, out, _ = self.cy("recall", "preference", "--person", "person-ellie", "--json")
        self.assertEqual(0, code)
        self.assertEqual("mem_preferences", json.loads(out)["query"])
        self.assertEqual(["Sign emails."], [r["body"] for r in json.loads(out)["rows"]])

    def test_failed_backlink_is_repaired_on_rerun(self):
        """Review of #183: a lost supersededBy must not leave two current records for one key."""
        from camayoc_cli import quipu
        base = ["record", "preference", "--person", "person-robin", "--context", "document", "--topic", "board-access"]
        code, out, err = self.cy(*base, "--prefers", "Short documents.", now="2026-10-10T00:00:00Z")
        self.assertEqual(0, code, err)
        old = out.split()[-1]
        real, calls = quipu.Client.knot, []

        def flaky(client, *args, **kwargs):
            calls.append(args)
            if len(calls) == 2:  # the back-link write
                raise quipu.QuipuError("injected failure")
            return real(client, *args, **kwargs)

        with mock.patch.object(quipu.Client, "knot", flaky):
            code, out, err = self.cy(*base, "--prefers", "Shorter documents.", now="2026-10-11T00:00:00Z")
        self.assertEqual(1, code)
        self.assertIn("Re-run the same command to finish", err)
        recall = ["recall", "preference", "--person", "person-robin", "--context", "document", "--json"]
        keys = [r["naturalKey"] for r in json.loads(self.cy(*recall)[1])["rows"]]
        self.assertEqual(2, keys.count(keys[0]), "precondition: the failure left two current records")
        code, out, err = self.cy(*base, "--prefers", "Shorter documents.", now="2026-10-11T00:00:05Z")
        self.assertEqual(0, code, err)
        self.assertIn(f"repaired: {old} marked superseded by ", out)
        self.assertIn("unchanged ", out)
        rows = json.loads(self.cy(*recall)[1])["rows"]
        self.assertEqual(["Shorter documents."], [r["body"] for r in rows])

    def test_index_since_feeds_the_bobbin_mirror(self):
        """bobbin gshu.2 contract: /ask with only `since`, pages of 50 + sentinel, superseded comes back once."""
        self.post("/queries", json.loads((ROOT / "queries/mem_index_since.json").read_text()))
        for i in range(55):
            code, _, err = self.cy("record", "handoff", "--agent", f"pager-{i:02d}", "--next", f"step {i}",
                                   now=f"2026-11-01T00:{i:02d}:00Z")
            self.assertEqual(0, code, err)
        code, out, _ = self.cy("record", "handoff", "--agent", "pager-00", "--next", "step again",
                               now="2026-11-01T01:00:00Z")
        old, new = out.split()[1], out.split()[3]
        rows, pages = [], 0
        since = "2026-10-31T23:59:59Z"  # only this test's records
        while True:
            page = self.post("/ask", {"name": "mem_index_since", "params": {"since": since}})["rows"]
            pages += 1
            rows += page[:50]
            if len(page) <= 50:
                break
            since = page[49]["cursor"]
        self.assertEqual(2, pages)
        by_iri = {}
        for r in rows:
            by_iri.setdefault(r["iri"], []).append(r)
        self.assertEqual(["superseded"], [r["status"] for r in by_iri[old]])
        self.assertEqual(f"2026-11-01T01:00:00Z|{old}", by_iri[old][0]["cursor"])
        self.assertEqual(["current"], [r["status"] for r in by_iri[new]])
        self.assertEqual("step again", by_iri[new][0]["body"])
        current = [r for r in rows if r["status"] == "current"]
        self.assertEqual(55, len(current))
        self.assertEqual(sorted(r["cursor"] for r in rows), [r["cursor"] for r in rows])
        for col in ("naturalKey", "kind", "label", "sourceKind", "recordedAt"):
            self.assertTrue(all(r.get(col) for r in current), col)

    def test_index_since_pages_records_sharing_a_timestamp(self):
        """Review of bobbin #216: ties on recordedAt must not fall between pages."""
        self.post("/queries", json.loads((ROOT / "queries/mem_index_since.json").read_text()))
        for i in range(45):  # rows 1-45 of the window, distinct instants
            code, _, err = self.cy("record", "handoff", "--agent", f"tie-{i:02d}", "--next", f"early {i}",
                                   now=f"2026-12-01T00:{i:02d}:00Z")
            self.assertEqual(0, code, err)
        tied = []
        for i in range(45, 60):  # rows 46-60 share one instant, straddling the 50-row page
            code, out, err = self.cy("record", "handoff", "--agent", f"tie-{i:02d}", "--next", f"tied {i}",
                                     now="2026-12-01T01:00:00Z")
            self.assertEqual(0, code, err)
            tied.append(out.split()[-1])
        rows, since, pages = [], "2026-11-30T23:59:59Z", 0
        while True:
            page = self.post("/ask", {"name": "mem_index_since", "params": {"since": since}})["rows"]
            pages += 1
            rows += page[:50]
            if len(page) <= 50:
                break
            since = page[49]["cursor"]
        self.assertEqual(2, pages)
        iris = [r["iri"] for r in rows]
        self.assertEqual(60, len(iris))
        self.assertEqual(len(iris), len(set(iris)), "no row repeats across pages")
        self.assertEqual(sorted(tied), [r["iri"] for r in rows if r["cursor"].startswith("2026-12-01T01:00:00Z|")])
        first_page_ties = [r for r in rows[:50] if r["cursor"].startswith("2026-12-01T01:00:00Z|")]
        self.assertEqual(5, len(first_page_ties), "the tie straddles the page boundary")

    def test_late_repair_still_reaches_the_feed(self):
        """A supersession written after the reader's cursor must still come back as `superseded`."""
        self.post("/queries", json.loads((ROOT / "queries/mem_index_since.json").read_text()))
        from camayoc_cli import quipu
        base = ["record", "preference", "--person", "person-kai", "--context", "chat", "--topic", "chat-signoff"]
        code, out, err = self.cy(*base, "--prefers", "Terse.", now="2026-12-05T00:00:00Z")
        self.assertEqual(0, code, err)
        old = out.split()[-1]
        real, calls = quipu.Client.knot, []

        def flaky(client, *args, **kwargs):
            calls.append(args)
            if len(calls) == 2:
                raise quipu.QuipuError("injected failure")
            return real(client, *args, **kwargs)

        with mock.patch.object(quipu.Client, "knot", flaky):
            self.cy(*base, "--prefers", "Terser.", now="2026-12-05T00:01:00Z")

        def pull(since):
            rows = []
            while True:
                page = self.post("/ask", {"name": "mem_index_since", "params": {"since": since}})["rows"]
                rows += page[:50]
                if not page:
                    return rows, since
                since = page[min(len(page), 50) - 1]["cursor"]
                if len(page) <= 50:
                    return rows, since

        _, cursor = pull("")  # the mirror is now past both versions
        code, out, err = self.cy(*base, "--prefers", "Terser.", now="2026-12-06T00:00:00Z")
        self.assertIn(f"repaired: {old}", out)
        rows, _ = pull(cursor)
        self.assertEqual([("superseded", f"2026-12-06T00:00:00Z|{old}")],
                         [(r["status"], r["cursor"]) for r in rows if r["iri"] == old])

    def test_prime_live_counts(self):
        with mock.patch.dict(os.environ, self.env):
            from camayoc_cli import prime
            state = prime.live()
        self.assertEqual("ok", state["status"])
        self.assertIn("current_records", state["summary"])
        self.assertEqual("absent", state["summary"]["credential"])


if __name__ == "__main__":
    unittest.main()
