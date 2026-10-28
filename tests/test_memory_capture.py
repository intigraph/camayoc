"""Memory capture by code and promotion to typed entities (aegis-gshu.4).

docs/design/memory-capture.md. The offline class needs no store; the
integration class drives a throwaway local quipu-server, never production.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_cy_cli as base  # noqa: E402
from quipu_bin_guard import QUIPU_SERVER as QUIPU_SERVER_BIN, requires_quipu_server  # noqa: E402

from camayoc_cli import capture, cli, memory, planes, promote, quipu  # noqa: E402

A = base.A
STAMP = "2026-10-28T12:00:00Z"


def offline_env(tmp: str) -> dict:
    return {"QUIPU_SERVER": f"http://127.0.0.1:{base.free_port()}", "XDG_STATE_HOME": tmp,
            "XDG_CONFIG_HOME": tmp, "QUIPU_AUTH_TOKEN": "", "QUIPU_AUTH_TOKEN_FILE": str(Path(tmp) / "none"),
            "SHANTY_AGENT": "tester", "CAMAYOC_NOW": STAMP}


class Isolated(unittest.TestCase):
    """Every test here runs with os.environ pointed at a dead local port, so no
    code path can fall back to the operator's QUIPU_SERVER or token (a first
    draft of these tests did, and wrote 6 captures to production; they were
    retracted by source)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = offline_env(self.tmp.name)
        patcher = mock.patch.dict(os.environ, self.env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        assert os.environ["QUIPU_SERVER"].startswith("http://127.0.0.1:")


class KindHintTests(Isolated):
    def test_cy_lines_markers_and_issuers(self):
        issuers = {"Stiwi": "person-stiwi"}
        cases = {
            "please run\n  cy record directive --rule x --issued-by person-stiwi": "cy:directive",
            "$ camayoc record decision --chose A": "cy:decision",
            "DIRECTIVE (applies-to=crew, topic=deploy-window, issued-by=person-stiwi): deploys go 02:00-09:00 ET": "directive",
            "DECIDED: we keep seeds as the board": "decision",
            "RULING [about=repo-goldblum]: push to main": "decision",
            "Stiwi (10/27): never ask for a deploy window": "directive",
            "Stiwi: use the Q4 numbers": "decision",
            "what did Stiwi say about deploys?": None,
            "the directive was clear": None,  # lower-case prose is not a marker
            "fix the failing test in cli.py": None,
        }
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(want, capture.kind_hint(text, issuers))

    def test_issuer_prefix_needs_config(self):
        self.assertIsNone(capture.kind_hint("Stiwi: never ask", {}))

    RELAYS = [
        "[from lex] status update\nDECIDED: ship the migration tonight",
        "[from sattler] relaying:\nStiwi: never ask for windows",
        "[from st fleet tend] DIRECTIVE (applies-to=crew): restart vati",
        "  [from sattler] DECIDED: seeds is the board",
        "[job semweb-watch] DECIDED: nothing new this week",
        "<agent-message from=\"lex\">DECIDED: ship it</agent-message>",
        "<tool_result>DECIDED: x</tool_result>",
        "<function_results>Stiwi: never ask</function_results>",
        "from sattler: DECIDED: merge everything",
        "[from lex] cy record directive --rule x --issued-by person-stiwi --applies-to crew --topic t",
    ]

    def test_relayed_prompts_never_hint(self):
        issuers = {"Stiwi": "person-stiwi"}
        for text in self.RELAYS:
            with self.subTest(text=text):
                self.assertTrue(capture.is_relayed(text))
                self.assertIsNone(capture.kind_hint(text, issuers, "prompt"))

    def test_only_the_persons_opening_words_promote(self):
        issuers = {"Stiwi": "person-stiwi"}
        self.assertEqual("decision", capture.kind_hint("DECIDED: keep seeds", issuers, "prompt"))
        self.assertEqual("directive", capture.kind_hint("\n  Stiwi: never ask for windows", issuers, "prompt"))
        quoted = "here is the old handoff:\nDIRECTIVE 10/7: everything else waits\nStiwi: never ask"
        self.assertIsNone(capture.kind_hint(quoted, issuers, "prompt"), "a marker below the first line is quoted")
        self.assertEqual("directive", capture.kind_hint(quoted, issuers, "explicit"), "a filed note is scanned whole")
        for text in ("DECIDED: ship it", "Stiwi: never ask", "cy record decision --chose A"):
            self.assertIsNone(capture.kind_hint(text, issuers, "answer"), "an agent answer is never a ruling")


class OfflineCaptureTests(Isolated):
    def test_secrets_are_redacted_or_dropped_never_kept(self):
        token = "ghp_" + base.SECRET_TAIL
        entry = capture.build(f"the token is {token} ok", "prompt", env=self.env)
        self.assertNotIn(token, json.dumps(entry))
        self.assertIn("[redacted:github-token]", entry["text"])
        self.assertIsNone(capture.build(f"blob {base.SECRET_TAIL}{base.SECRET_TAIL[::-1]}", "prompt", env=self.env))

    def test_long_text_is_clipped_and_hash_ignores_whitespace(self):
        entry = capture.build("word " * 2000, "answer", env=self.env)
        self.assertLessEqual(len(entry["text"]), capture.MAX_TEXT)
        self.assertTrue(entry["text"].endswith("[clipped]"))
        self.assertEqual(capture.content_hash("a  b\nc"), capture.content_hash(" a b c "))

    def test_hooks_are_opt_in_and_silent(self):
        payload = {"session_id": "s1", "prompt": "DECIDED (about=x): a long enough prompt here"}
        out = capture.hook("user-prompt-submit", payload, env=self.env)
        self.assertEqual(["capture disabled (set CAMAYOC_CAPTURE=1)"], out.lines)
        self.assertEqual([], capture.spooled(self.env))

    def test_prompt_hook_spools_and_keeps_when_quipu_is_down(self):
        env = {**self.env, "CAMAYOC_CAPTURE": "1", "CAMAYOC_CAPTURE_SYNC": "1"}
        capture.hook("user-prompt-submit", {"session_id": "s1", "prompt": "short"}, env=env)
        capture.hook("user-prompt-submit", {"session_id": "s1",
                                            "prompt": "<task-notification> agent finished its work"}, env=env)
        self.assertEqual([], capture.spooled(env), "short and harness prompts are skipped")
        capture.hook("user-prompt-submit", {"session_id": "s1", "prompt": "DECIDED: keep the board on seeds"}, env=env)
        spooled = capture.spooled(env)
        self.assertEqual(1, len(spooled))
        self.assertEqual(("prompt", "decision", "s1"), (spooled[0]["source"], spooled[0]["kind"], spooled[0]["session"]))

    def test_stop_hook_reads_the_final_answer_from_the_transcript(self):
        path = Path(self.tmp.name) / "t.jsonl"
        lines = [{"type": "user", "message": {"content": "q"}},
                 {"type": "assistant", "message": {"content": [{"type": "text", "text": "first"}]}},
                 {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash"}]}},
                 {"type": "assistant", "message": {"content": [{"type": "text", "text": "final answer"}]}},
                 {"type": "system", "subtype": "x"}]
        path.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
        self.assertEqual("final answer", capture.last_assistant_text(str(path)))
        env = {**self.env, "CAMAYOC_CAPTURE": "1", "CAMAYOC_CAPTURE_SYNC": "1"}
        capture.hook("stop", {"session_id": "s2", "transcript_path": str(path)}, env=env)
        capture.hook("stop", {"session_id": "s2", "last_assistant_message": "codex final"}, env=env)
        self.assertEqual(["final answer", "codex final"], [e["text"] for e in capture.spooled(env)])

    def test_hook_cli_never_fails_and_prints_nothing(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {**self.env, "CAMAYOC_CAPTURE": "1", "CAMAYOC_CAPTURE_SYNC": "1"}), \
                mock.patch("sys.stdin", io.StringIO("not json")), redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["hook", "user-prompt-submit"])
        self.assertEqual((0, ""), (code, out.getvalue()))

    def test_spool_files_are_private_even_in_an_existing_dir(self):
        d = capture.state_dir(self.env)
        d.mkdir(parents=True, mode=0o755)
        os.chmod(d, 0o755)
        capture.spool(capture.build("a long enough prompt to keep", "prompt", env=self.env), self.env)
        capture.flush(env=self.env)  # quipu is down: the in-flight file is rewritten
        self.assertEqual(0o700, d.stat().st_mode & 0o777)
        files = [p for p in d.iterdir()]
        self.assertTrue(files)
        for p in files:
            with self.subTest(file=p.name):
                self.assertEqual(0o600, p.stat().st_mode & 0o777)

    def test_retention_bounds_age_count_and_bytes(self):
        import datetime as dt
        now = dt.datetime(2026, 10, 28, 12, tzinfo=dt.timezone.utc)
        old = [{"at": "2026-10-01T00:00:00Z", "text": "old", "hash": "h"}] * 3
        fresh = [{"at": "2026-10-27T00:00:00Z", "text": f"n{i}", "hash": "h"} for i in range(10)]
        with mock.patch.object(capture, "MAX_ENTRIES", 4):
            kept, dropped = capture.retain(old + fresh, now)
        self.assertEqual((["n6", "n7", "n8", "n9"], 9), ([e["text"] for e in kept], dropped))
        big = [{"at": "2026-10-27T00:00:00Z", "text": "x" * 1000, "hash": str(i)} for i in range(10)]
        with mock.patch.object(capture, "MAX_BYTES", 3500):
            kept, _ = capture.retain(big, now)
        self.assertEqual(["7", "8", "9"], [e["hash"] for e in kept], "oldest dropped first")

    def test_outage_cannot_pile_up_captures(self):
        with mock.patch.object(capture, "MAX_ENTRIES", 5):
            for i in range(12):
                capture.spool(capture.build(f"prompt number {i} long enough", "prompt", env=self.env), self.env)
                capture.flush(env=self.env)  # quipu down each time
            self.assertEqual(5, len(capture.spooled(self.env)))
        with mock.patch.object(capture, "HARD_BYTES", 0):
            self.assertFalse(capture.spool(capture.build("one more long enough prompt", "prompt", env=self.env),
                                           self.env), "past the hard ceiling the hook drops, never grows")

    def test_hook_returns_fast_while_a_flush_is_mid_network(self):
        import threading

        class Slow:
            calls = 0

            def knot(self, *a, **k):
                Slow.calls += 1
                time.sleep(1.5)
                return {"conforms": True}

        capture.spool(capture.build("first capture, long enough", "prompt", env=self.env), self.env)
        t = threading.Thread(target=capture.flush, kwargs={"client": Slow(), "env": self.env})
        t.start()
        time.sleep(0.2)  # the flush now holds its lock and is inside knot()
        self.assertTrue(capture.flush_running(self.env))
        env = {**self.env, "CAMAYOC_CAPTURE": "1"}
        started = time.monotonic()
        out = capture.hook("user-prompt-submit", {"session_id": "s", "prompt": "a prompt during the flush"}, env=env)
        elapsed = time.monotonic() - started
        t.join()
        self.assertEqual(["spooled"], out.lines)
        self.assertLess(elapsed, 0.5, "the hook never waits on the flush")
        self.assertEqual(1, Slow.calls)
        self.assertEqual(["a prompt during the flush"], [e["text"] for e in capture.spooled(self.env)],
                         "the new capture waits in the fresh spool; the flushed one is gone")

    def test_turtle_is_untyped(self):
        entry = capture.build("RULING: x y z", "explicit", session="abc", env=self.env)
        ttl = capture.turtle(entry)
        self.assertNotIn(" a <", ttl, "a capture is untyped: no class, no shape, never refused for its kind")
        self.assertIn(f"<{A}kindHint> \"decision\"", ttl)
        self.assertIn("urn:camayoc:session:claude:abc", ttl)
        self.assertTrue(capture.capture_iri(entry).startswith(f"{A}mem/capture/20261028T120000Z-"))


class OfflineFillTests(Isolated):
    def test_cy_line_is_parsed_by_cy_itself(self):
        text = "Recording it:\n cy record directive --rule 'Deploys go in the window.' \\\n --issued-by person-stiwi " \
               "--applies-to crew --topic deploy-window\nthanks"
        entity, values, flags, sup = promote.fill_cy(text)
        self.assertEqual("directive", entity)
        self.assertEqual(("Deploys go in the window.", "person-stiwi", "crew", "deploy-window"),
                         (values["rule"], values["issuedBy"], values["appliesTo"], values["topic"]))
        with self.assertRaises(promote.Unfillable):
            promote.fill_cy("cy record nonsense --x y")

    def test_marker_params_issuer_and_defaults(self):
        cap = {"c": f"{A}mem/capture/x", "kind": "directive", "src": "prompt", "at": STAMP,
               "text": "DIRECTIVE (applies-to=crew, topic=deploy-window): disruptive deploys go 02:00-09:00 ET.\n\nmore"}
        entity, values, _, _ = promote.fill_marker(cap, {"prompt_author": "person-stiwi"})
        self.assertEqual("directive", entity)
        self.assertEqual("disruptive deploys go 02:00-09:00 ET.", values["rule"])
        self.assertEqual(("person-stiwi", "crew", "deploy-window", "2026-10-28"),
                         (values["issuedBy"], values["appliesTo"], values["topic"], values["issuedOn"]))
        cap = {**cap, "kind": "decision", "src": "explicit", "text": "Stiwi (10/27): use seeds"}
        _, values, _, _ = promote.fill_marker(cap, {"issuers": {"Stiwi": "person-stiwi"}})
        self.assertEqual(("use seeds", "person-stiwi"), (values["chose"], values["by"]))
        with self.assertRaises(promote.Unfillable):
            promote.fill_marker({**cap, "text": "DECIDED (colour=blue): x"}, {})

    def test_relayed_prompt_and_answers_never_fill_even_with_prompt_author(self):
        config = {"prompt_author": "person-stiwi", "issuers": {"Stiwi": "person-stiwi"}}
        for text in KindHintTests.RELAYS[:3]:
            cap = {"c": f"{A}mem/capture/x", "kind": "decision", "src": "prompt", "at": STAMP, "text": text}
            with self.subTest(text=text), self.assertRaises(promote.Unfillable):
                promote.fill_marker(cap, config)
        with self.assertRaises(promote.Unfillable):
            promote.fill_marker({"c": "x", "kind": "decision", "src": "answer", "at": STAMP,
                                 "text": "DECIDED (about=a, topic=b): x"}, config)

    def test_extractor_is_off_by_default_and_never_claude(self):
        self.assertIsNone(promote.extractor_argv({}, env={}))
        self.assertEqual(["codex", "exec"], promote.extractor_argv({"extractor": {"argv": ["codex", "exec"]}}, env={}))
        self.assertIsNone(promote.extractor_argv({"extractor": {"argv": ["codex"]}},
                                                 env={"CAMAYOC_PROMOTE_EXTRACT": "0"}))
        for argv in (["claude", "-p"], ["llm", "-m", "anthropic/x"]):
            with self.assertRaises(ValueError):
                promote.extractor_argv({"extractor": {"argv": argv}}, env={})

    def test_adapter_describe_and_route(self):
        d = promote.adapter(True, None)
        self.assertEqual(1, d["version"])
        self.assertEqual([planes.plane_for("inferred")], d["graphs"])
        self.assertIn(f"{A}kindHint", d["attributes"])
        cap = f"{A}mem/capture/20261028T120000Z-abc"
        routed = promote.adapter(False, {"now": 1, "route": [{"entity": cap}, {"entity": f"{A}other"}, {}]})
        self.assertEqual({"version": 1, "items": [cap]}, routed)


@requires_quipu_server
class LocalPromotionTests(unittest.TestCase):
    """Capture -> promote against a throwaway local quipu-server."""

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.port = base.free_port()
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
        cls.post("/shapes", {"action": "load", "name": "test-person", "turtle": base.TEST_SHAPES})
        for kind in ("declared", "observed", "inferred"):
            g = planes.plane_for(kind)
            cls.post("/graph/create", {"graph": g})
            cls.post("/graph/label", {"graph": g, "timestamp": "2026-01-01T00:00:00Z", "actor": "test",
                                      "trust": {"iri": g + "/trust", "chain": "urn:test:chain",
                                                "rank": {"declared": 30, "observed": 20, "inferred": 0}[kind]}})
        seed = "".join(f"<{A}{n}> <http://www.w3.org/2000/01/rdf-schema#label> \"{n}\" .\n"
                       for n in ("repo-goldblum", "hammond"))
        seed += f"<{A}person-stiwi> a <{A}Person> ; <http://www.w3.org/2000/01/rdf-schema#label> \"Stiwi\" .\n"
        cls.post("/knot", {"turtle": seed, "actor": "test"})
        cls.state = tempfile.TemporaryDirectory()
        Path(cls.state.name, "camayoc").mkdir()
        Path(cls.state.name, "camayoc", "capture.json").write_text(json.dumps(
            {"issuers": {"Stiwi": "person-stiwi"}, "prompt_author": "person-stiwi"}))
        cls.env = {"QUIPU_SERVER": cls.base, "QUIPU_AUTH_TOKEN": "", "SHANTY_AGENT": "tester",
                   "QUIPU_AUTH_TOKEN_FILE": str(Path(cls.state.name) / "no-token"),
                   "XDG_STATE_HOME": cls.state.name, "XDG_CONFIG_HOME": cls.state.name,
                   "CAMAYOC_NOW": STAMP, "CAMAYOC_CAPTURE": "1", "CAMAYOC_CAPTURE_SYNC": "1"}
        cls.patcher = mock.patch.dict(os.environ, cls.env)
        cls.patcher.start()
        assert os.environ["QUIPU_SERVER"].startswith("http://127.0.0.1:")
        for topic in ("deploy-window", "board", "push-policy", "dup-test"):
            code, _, err = base.run_cy("topic", "add", topic, env=cls.env)
            assert code == 0, err

    @classmethod
    def tearDownClass(cls):
        cls.patcher.stop()
        cls.proc.terminate()
        cls.proc.wait(timeout=5)
        cls.temp.cleanup()
        cls.state.cleanup()

    @classmethod
    def post(cls, path, payload=None):
        return base.LocalStoreTests.post.__func__(cls, path, payload)

    def env_at(self, now):
        return {**self.env, "CAMAYOC_NOW": now}

    def note(self, text, now, **kw):
        with mock.patch.dict(os.environ, self.env_at(now)):
            return capture.note(text, env=self.env_at(now), **kw)

    def promote(self, now, **kw):
        with mock.patch.dict(os.environ, self.env_at(now)):
            return promote.run(env=self.env_at(now), **kw)

    def facts(self, iri):
        rows = quipu.Client(self.base).query(
            f"SELECT ?p ?o FROM <{quipu.ROOT_GRAPH}> FROM <{planes.plane_for('inferred')}> "
            f"FROM <{planes.plane_for('declared')}> FROM <{planes.plane_for('observed')}> "
            f"WHERE {{ <{iri}> ?p ?o }}")
        out: dict = {}
        for r in rows:
            out.setdefault(quipu.value(r["p"]), []).append(quipu.value(r["o"]))
        return out

    def test_cy_shaped_capture_promotes_and_links_both_ways(self):
        out = self.note("Recording: cy record directive --rule 'Push straight to main; rebase first.' "
                        "--issued-by person-stiwi --applies-to repo-goldblum --topic push-policy",
                        "2026-10-28T09:00:00Z")
        self.assertEqual("captured", out.data["status"], out.lines)
        cap = out.data["iri"]
        res = self.promote("2026-10-28T09:01:00Z")
        [r] = [x for x in res.data["results"] if x["capture"] == cap]
        self.assertEqual("recorded", r["status"], res.lines)
        self.assertEqual([r["iri"]], self.facts(cap)[f"{A}promotedTo"])
        record = self.facts(r["iri"])
        self.assertEqual([cap], record["http://www.w3.org/ns/prov#wasDerivedFrom"])
        self.assertEqual(["declared"], record[f"{A}sourceKind"])
        again = self.promote("2026-10-28T09:02:00Z")
        self.assertNotIn(cap, [x["capture"] for x in again.data["results"]], "a promoted capture is settled")

    def test_marker_supersedes_and_duplicates_collapse(self):
        first = self.note("DIRECTIVE (applies-to=crew, topic=deploy-window, issued-by=person-stiwi): deploys go 02:00-09:00 ET.",
                          "2026-10-20T10:00:00Z")
        r1 = self.promote("2026-10-20T10:01:00Z", only=[first.data["iri"]]).data["results"][0]
        self.assertEqual("recorded", r1["status"])
        # the same words again within 30 days: a duplicate capture, no new record
        dup = self.note("DIRECTIVE (applies-to=crew, topic=deploy-window, issued-by=person-stiwi):  deploys go 02:00-09:00 ET.",
                        "2026-10-21T10:00:00Z")
        r2 = self.promote("2026-10-21T10:01:00Z", only=[dup.data["iri"]]).data["results"][0]
        self.assertEqual(("duplicate", first.data["iri"]), (r2["status"], r2["of"]))
        # a new rule on the same key supersedes; it does not pile up
        new = self.note("DIRECTIVE (applies-to=crew, topic=deploy-window, issued-by=person-stiwi): deploys go 01:00-08:00 ET.",
                        "2026-10-22T10:00:00Z")
        r3 = self.promote("2026-10-22T10:01:00Z", only=[new.data["iri"]]).data["results"][0]
        self.assertEqual("superseded", r3["status"], r3)
        self.assertEqual([r3["iri"]], self.facts(r1["iri"])[f"{A}supersededBy"])
        code, out, _ = base.run_cy("recall", "directive", "--applies-to", "crew", "--json",
                                   env=self.env_at("2026-10-22T11:00:00Z"))
        rows = [x for x in json.loads(out)["rows"] if x["naturalKey"].endswith("/deploy-window")]
        self.assertEqual([r3["iri"]], [x["iri"] for x in rows])

    def test_incomplete_ruling_is_blocked_with_the_finishing_call(self):
        out = self.note("Stiwi: keep the work board on seeds", "2026-10-28T10:00:00Z")
        cap = out.data["iri"]
        res = self.promote("2026-10-28T10:01:00Z", only=[cap])
        r = res.data["results"][0]
        self.assertEqual("blocked", r["status"])
        self.assertTrue(any("--about" in p for p in r["problems"]), r["problems"])
        self.assertIn("cy record decision --chose 'keep the work board on seeds' --by person-stiwi --about <about>",
                      r["suggested"])
        facts = self.facts(cap)
        self.assertIn(f"{A}promotionBlocked", facts)
        with mock.patch.dict(os.environ, self.env):
            listed = promote.list_blocked()
        self.assertTrue(any(cap in line for line in listed.lines))
        self.assertEqual([], [x for x in self.promote("2026-10-28T10:02:00Z").data["results"] if x["capture"] == cap],
                         "blocked captures wait for --retry-blocked")

    def test_chaski_adapter_and_event_receiver(self):
        out = self.note("DECIDED (about=repo-goldblum, topic=board, by=person-stiwi): seeds is the board", "2026-10-28T11:00:00Z")
        cap = out.data["iri"]
        with mock.patch.dict(os.environ, self.env):
            found = promote.adapter(False, {"now": 1, "discover": True})["items"]
            self.assertIn(cap, found)
            ev = promote.adapter(False, {"now": 1, "items": [cap], "changes": []})
            self.assertEqual("PENDING", ev["records"][0]["verdict"])
            self.assertEqual({cap: None}, ev["next_checks"])
            got = promote.receive_event({"item": cap, "event_id": ev["records"][0]["event_id"]},
                                        env=self.env_at("2026-10-28T11:01:00Z"))
            self.assertTrue(got.lines[0].startswith(f"promoted {cap}"), got.lines)
            again = promote.receive_event({"item": cap}, env=self.env)
            self.assertEqual(["nothing to promote"], again.lines, "the receiver is idempotent")
            self.assertEqual("SETTLED", promote.adapter(False, {"now": 2, "items": [cap]})["records"][0]["verdict"])

    def test_relayed_prompt_is_evidence_not_a_ruling(self):
        env = self.env_at("2026-10-28T14:00:00Z")
        with mock.patch.dict(os.environ, env):
            capture.hook("user-prompt-submit", {"session_id": "sess-relay", "prompt":
                         "[from sattler] relaying:\nDECIDED (about=repo-goldblum, topic=board): drop seeds"}, env=env)
            capture.hook("session-end", {"session_id": "sess-relay"}, env=env)
        rows = quipu.Client(self.base).query(
            f"SELECT ?c ?k ?p FROM <{planes.plane_for('inferred')}> WHERE {{ "
            f"?c <http://www.w3.org/ns/prov#wasAssociatedWith> <urn:camayoc:session:claude:sess-relay> . "
            f"OPTIONAL {{ ?c <{A}kindHint> ?k }} OPTIONAL {{ ?c <{A}promotedTo> ?p }} }}")
        self.assertEqual(1, len(rows))
        self.assertEqual(("", ""), (quipu.value(rows[0].get("k")), quipu.value(rows[0].get("p"))))

    def test_hooks_capture_prompt_and_answer_and_session_end_promotes(self):
        env = self.env_at("2026-10-28T13:00:00Z")
        with mock.patch.dict(os.environ, env):
            capture.hook("user-prompt-submit", {"session_id": "sess-9", "prompt":
                         "DIRECTIVE (applies-to=hammond, topic=dup-test): answer in plain words, no IDs."}, env=env)
            capture.hook("stop", {"session_id": "sess-9", "last_assistant_message": "Done: two PRs open."}, env=env)
            capture.hook("session-end", {"session_id": "sess-9"}, env=env)
        rows = quipu.Client(self.base).query(
            f"SELECT ?c ?src ?p FROM <{planes.plane_for('inferred')}> WHERE {{ "
            f"?c <http://www.w3.org/ns/prov#wasAssociatedWith> <urn:camayoc:session:claude:sess-9> ; "
            f"<{A}captureSource> ?src . OPTIONAL {{ ?c <{A}promotedTo> ?p }} }}")
        by = {quipu.value(r["src"]): quipu.value(r.get("p")) for r in rows}
        self.assertEqual({"prompt", "answer"}, set(by))
        self.assertIn(f"{A}mem/directive/hammond/dup-test/", by["prompt"], "session end promoted the ruling")
        self.assertEqual("", by["answer"], "a plain answer stays evidence")
        self.assertEqual([], capture.spooled(env))


if __name__ == "__main__":
    unittest.main()
