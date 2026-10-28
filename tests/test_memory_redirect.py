"""Memory-file redirect hook (`cy hook memory-redirect`), aegis-gshu.1.

docs/design/agent-memory-entities.md §5-§6. Fixture memory files live in
tests/fixtures/memory-redirect/. The secret fixture is assembled at runtime, so
no credential-shaped literal sits in the repository. The store tests drive a
throwaway local quipu-server, never a production store.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from quipu_bin_guard import QUIPU_SERVER as QUIPU_SERVER_BIN, requires_quipu_server  # noqa: E402

from camayoc_cli import cli, memory, memory_redirect as mr, planes as cy_planes  # noqa: E402
from test_cy_cli import A, SECRET_TAIL, TEST_SHAPES, free_port  # noqa: E402

FIXTURES = ROOT / "tests/fixtures/memory-redirect"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def secret_memory() -> tuple[str, str]:
    token = "ghp_" + SECRET_TAIL
    text = ("---\nname: pane-read\ndescription: Reach the pane with the deploy credential " + token
            + "\nmetadata:\n  type: feedback\n---\nAlways use it.\n")
    return text, token


class Sandbox:
    """A fake HOME with a Claude project memory dir, a state dir and an unreachable store."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.memdir = base / "home/.claude/projects/-work-proj/memory"
        self.memdir.mkdir(parents=True)
        self.env = {"XDG_STATE_HOME": str(base / "state"), "HOME": str(base / "home"),
                    "QUIPU_SERVER": f"http://127.0.0.1:{free_port()}", "QUIPU_AUTH_TOKEN": "",
                    "QUIPU_AUTH_TOKEN_FILE": str(base / "no-token"), "SHANTY_AGENT": "hammond",
                    "CAMAYOC_PERSON": "person-stiwi", "CAMAYOC_NOW": "2026-10-28T12:00:00Z"}

    def path(self, name: str) -> str:
        return str(self.memdir / name)

    def log(self) -> list[dict]:
        p = mr.log_path(self.env)
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []

    def close(self):
        self.tmp.cleanup()


def hook(payload, *argv: str, env: dict) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    with mock.patch.dict(os.environ, env), mock.patch.object(sys, "stdin", io.StringIO(raw)), \
            mock.patch.object(mr, "spawn_worker") as spawn, \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(["hook", "memory-redirect", *argv])
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 2
    hook.spawned = [c.args[0] for c in spawn.call_args_list]
    return code, out.getvalue(), err.getvalue()


def write(path: str, content: str) -> dict:
    return {"tool_name": "Write", "tool_input": {"file_path": path, "content": content},
            "hook_event_name": "PreToolUse"}


class PathAndParseTests(unittest.TestCase):

    def test_memory_paths_match_every_file_not_just_the_index(self):
        env = {"HOME": "/Users/u"}
        for p in ("/Users/u/.claude/projects/-a-b/memory/MEMORY.md",
                  "/Users/u/.claude/projects/-a-b/memory/handoff-2026-10-27.md",
                  "/Users/u/.claude/projects/x/memory/sub/notes.txt",
                  "/Users/u/.claude/projects/x/../x/memory/n.md"):
            self.assertTrue(mr.is_memory_path(p, env), p)
        for p in ("/Users/u/.claude/projects/-a-b/transcript.jsonl", "/Users/u/.claude/memory/x.md",
                  "/repo/docs/memory/x.md", "/Users/u/.claude/projects/-a-b/memory", "",
                  "/repo/fixtures/.claude/projects/p/memory/x.md",
                  "/Users/other/.claude/projects/p/memory/x.md"):
            self.assertFalse(mr.is_memory_path(p, env), p)

    def test_claude_config_dir_is_honored(self):
        env = {"HOME": "/Users/u", "CLAUDE_CONFIG_DIR": "/cfg/claude"}
        self.assertTrue(mr.is_memory_path("/cfg/claude/projects/p/memory/x.md", env))
        self.assertTrue(mr.is_memory_path("/Users/u/.claude/projects/p/memory/x.md", env))

    def test_frontmatter_subset(self):
        fm, body = mr.parse_frontmatter(fixture("push-policy.md"))
        self.assertEqual("feedback", fm["metadata"]["type"])
        self.assertEqual("directive", fm["metadata"]["camayoc"]["entity"])
        self.assertEqual("repo-goldblum", fm["metadata"]["camayoc"]["appliesTo"])
        self.assertEqual("Free text the explicit block overrides.", body)
        fm, _ = mr.parse_frontmatter("---\nname: x\ntags:\n  - a\n  - 'b'\n---\nbody")
        self.assertEqual(["a", "b"], fm["tags"])
        self.assertEqual(({}, "no frontmatter"), mr.parse_frontmatter("no frontmatter"))

    def test_each_fixture_maps_to_its_entity(self):
        env = {"SHANTY_AGENT": "hammond", "CAMAYOC_PERSON": "person-stiwi"}
        cases = {
            "deploy-window.md": ("directive", {"rule": "Run disruptive deploys between 02:00 and 09:00 ET; never ask "
                                                       "for a window", "issuedBy": "person-stiwi",
                                               "appliesTo": "hammond", "topic": "deploy-window",
                                               "issuedOn": "2026-10-27"}),
            "chat-signoff.md": ("preference", {"person": "person-stiwi", "context": "chat", "topic": "chat-signoff"}),
            "pane-read.md": ("reference", {"topic": "pane-read",
                                           "locator": "ssh host-vati tmux capture-pane -p -t agent"}),
            "handoff-2026-10-27.md": ("handoff", {"agent": "hammond"}),
            "push-policy.md": ("directive", {"rule": "Push straight to main on goldblum; rebase first.",
                                             "appliesTo": "repo-goldblum", "topic": "push-policy"}),
            "board-access.md": ("decision", {"by": "person-stiwi", "about": "hammond", "topic": "board-access"}),
        }
        for name, (entity, subset) in cases.items():
            with self.subTest(fixture=name):
                p = mr.plan(fixture(name), name, env=env)
                self.assertEqual(entity, p["entity"])
                for k, v in subset.items():
                    self.assertEqual(v, p["values"].get(k), k)
        p = mr.plan(fixture("handoff-2026-10-27.md"), "handoff-2026-10-27.md", env=env)
        self.assertEqual(3, len(p["values"]["next"]), "top-level bullets only")
        p = mr.plan(fixture("deploy-window.md"), "deploy-window.md", env=env)
        self.assertIn("round trip", p["values"]["why"])
        self.assertIn("morning slot", p["values"]["verbatim"])
        self.assertNotIn("subject", mr.plan(fixture("pane-read.md"), "pane-read.md", env=env)["values"],
                         "an underivable key part is left for cy to refuse in plain words")

    def test_codex_patch_parse(self):
        patch = ("*** Begin Patch\n*** Add File: /h/.claude/projects/p/memory/new.md\n+---\n+name: n\n+---\n+body\n"
                 "*** Update File: notes.md\n*** Move to: /h/.claude/projects/p/memory/moved.md\n@@\n-a\n+b\n"
                 "*** Delete File: /h/.claude/projects/p/memory/old.md\n*** End Patch")
        got = mr.parse_patch(patch)
        self.assertEqual(("/h/.claude/projects/p/memory/new.md", "---\nname: n\n---\nbody\n", "add"), got[0])
        self.assertEqual(("/h/.claude/projects/p/memory/moved.md", None, "update"), got[1])
        self.assertEqual("delete", got[2][2])
        ts = mr.targets({"tool_name": "apply_patch", "tool_input": {"command": patch}, "cwd": "/repo"},
                        {"HOME": "/h"})
        self.assertEqual(3, len(ts))


class HookTests(unittest.TestCase):

    def setUp(self):
        self.box = Sandbox()
        self.env = self.box.env

    def tearDown(self):
        self.box.close()

    def test_non_memory_paths_and_garbage_pass_silently_in_every_mode(self):
        for mode in ("copy", "block"):
            for payload in (write("/repo/README.md", "x"), write("/repo/t/.claude/projects/p/memory/a.md", "x"), {"tool_name": "Bash", "tool_input": {"command": "ls"}},
                            "not json", "", "[]", {"tool_name": "Write", "tool_input": "oops"}):
                with self.subTest(mode=mode, payload=str(payload)[:30]):
                    code, out, err = hook(payload, "--mode", mode, env=self.env)
                    self.assertEqual((0, "", ""), (code, out, err))
        self.assertEqual({"error"}, {r["event"] for r in self.box.log()}, "unreadable input is logged, never fired")

    def test_off_mode_and_env_kill_switch(self):
        payload = write(self.box.path("deploy-window.md"), fixture("deploy-window.md"))
        self.assertEqual((0, "", ""), hook(payload, "--mode", "off", env=self.env))
        self.assertEqual((0, "", ""), hook(payload, "--mode", "block",
                                           env={**self.env, "CAMAYOC_MEMORY_REDIRECT": "off"}))
        self.assertEqual([], self.box.log())

    def test_copy_is_the_default_and_never_blocks(self):
        payload = write(self.box.path("deploy-window.md"), fixture("deploy-window.md"))
        code, out, err = hook(payload, env=self.env)
        self.assertEqual((0, "", ""), (code, out, err))
        self.assertEqual(1, len(hook.spawned), "one detached worker per memory file")
        self.assertEqual(["fired"], [r["event"] for r in self.box.log()])
        self.assertEqual("copy", self.box.log()[0]["mode"])

    def test_block_names_the_exact_cy_record_call(self):
        payload = write(self.box.path("deploy-window.md"), fixture("deploy-window.md"))
        code, out, err = hook(payload, "--mode", "block", env=self.env)
        self.assertEqual(2, code)
        self.assertEqual("", out)
        self.assertIn("cy record directive --rule 'Run disruptive deploys between 02:00 and 09:00 ET; never ask "
                      "for a window' --issued-by person-stiwi --applies-to hammond --topic deploy-window", err)
        code, _, err = hook(write(self.box.path("pane-read.md"), fixture("pane-read.md")), "--mode", "block",
                            env=self.env)
        self.assertIn("cy record reference --subject <subject> --topic pane-read", err)
        code, _, err = hook(write(self.box.path("MEMORY.md"), fixture("MEMORY.md")), "--mode", "block", env=self.env)
        self.assertEqual(2, code)
        self.assertIn("cy recall", err)

    def test_block_on_edit_suggests_from_the_edited_file(self):
        Path(self.box.path("deploy-window.md")).write_text(fixture("deploy-window.md"))
        payload = {"tool_name": "Edit", "tool_input": {"file_path": self.box.path("deploy-window.md"),
                                                        "old_string": "02:00 and 09:00", "new_string": "03:00 and 08:00"}}
        code, _, err = hook(payload, "--mode", "block", env=self.env)
        self.assertEqual(2, code)
        self.assertIn("between 03:00 and 08:00 ET", err)
        multi = {"tool_name": "MultiEdit", "tool_input": {"file_path": self.box.path("absent.md"),
                                                          "edits": [{"old_string": "zz", "new_string": "y"}]}}
        code, _, err = hook(multi, "--mode", "block", env=self.env)
        self.assertEqual(2, code)
        self.assertIn("cy record <entity>", err)

    def test_block_never_echoes_a_secret(self):
        text, token = secret_memory()
        code, out, err = hook(write(self.box.path("pane-read.md"), text), "--mode", "block", env=self.env)
        self.assertEqual(2, code)
        self.assertIn("<redacted: looks like a secret (github-token", err)
        self.assertNotIn(token, err + out)
        self.assertNotIn(token, mr.log_path(self.env).read_text())

    def test_codex_apply_patch_is_covered(self):
        patch = ("*** Begin Patch\n*** Add File: " + self.box.path("deploy-window.md") + "\n"
                 + "".join("+" + line + "\n" for line in fixture("deploy-window.md").splitlines()) + "*** End Patch")
        code, _, err = hook({"tool_name": "apply_patch", "tool_input": {"command": patch}}, "--mode", "block",
                            env=self.env)
        self.assertEqual(2, code)
        self.assertIn("cy record directive", err)

    def test_worker_queues_when_the_store_is_unreachable(self):
        payload = write(self.box.path("push-policy.md"), fixture("push-policy.md"))
        with mock.patch.dict(os.environ, self.env):
            job = mr.spool(mr.targets(payload)[0], person="person-stiwi", env=self.env)
            Path(self.box.path("push-policy.md")).write_text(fixture("push-policy.md"))
            got = mr.run_job(job, self.env)
        self.assertEqual("queued", got["event"])
        self.assertFalse(job.exists(), "the spool file (memory text) is removed once read")
        self.assertEqual(1, memory.queue_length(self.env))
        self.assertEqual("queued", self.box.log()[-1]["event"])

    def test_worker_refusal_is_logged_in_plain_words_without_the_secret(self):
        text, token = secret_memory()
        Path(self.box.path("s.md")).write_text(text)
        with mock.patch.dict(os.environ, self.env):
            got = mr.run_job(mr.spool(mr.targets(write(self.box.path("s.md"), text))[0], person="person-stiwi",
                                      env=self.env), self.env)
        self.assertEqual("refused", got["event"])
        logged = mr.log_path(self.env).read_text()
        self.assertIn("looks like a secret (github-token", logged)
        self.assertNotIn(token, logged)
        self.assertEqual(0, memory.queue_length(self.env), "a refused record is never queued")

    def test_a_denied_write_is_never_recorded(self):
        payload = write(self.box.path("push-policy.md"), fixture("push-policy.md"))
        with mock.patch.dict(os.environ, self.env):
            got = mr.run_job(mr.spool(mr.targets(payload)[0], person="person-stiwi", env=self.env), self.env,
                             timeout=0.3)
        self.assertEqual("skipped", got["event"])
        self.assertEqual(0, memory.queue_length(self.env))

    def test_worker_reads_an_edit_only_after_it_lands(self):
        path = self.box.path("deploy-window.md")
        Path(path).write_text("---\nname: deploy-window\n---\nold\n")
        payload = {"tool_name": "Edit", "tool_input": {"file_path": path, "old_string": "x", "new_string": "y"}}
        with mock.patch.dict(os.environ, self.env):
            job = mr.spool(mr.targets(payload)[0], person=None, env=self.env)
            self.assertEqual("skipped", mr.run_job(job, self.env, timeout=0.3)["event"])
            self.assertEqual("the write never landed", self.box.log()[-1]["reason"])
            job = mr.spool(mr.targets(payload)[0], person="person-stiwi", env=self.env)
            Path(path).write_text(fixture("deploy-window.md"))
            got = mr.run_job(job, self.env, timeout=2)
        self.assertEqual(("queued", "directive"), (got["event"], got["entity"]))

    def test_verify_nonce_maps_and_prechecks_without_a_store(self):
        env = {**self.env, mr.ENV_VERIFY: "nonce-123"}
        with mock.patch.dict(os.environ, env):
            job = mr.spool(mr.targets(write(self.box.path("push-policy.md"), fixture("push-policy.md")))[0],
                           person="person-stiwi", env=env)
            mr.run_job(job, env)
        self.assertEqual(0, memory.queue_length(env), "a verify probe never queues or writes")
        last = self.box.log()[-1]
        self.assertEqual(("verified", "directive", "nonce-123", 0), (last["event"], last["entity"], last["verify"],
                                                                    last["code"]))

    def test_real_hook_process_returns_at_once_and_its_worker_finishes(self):
        """End to end through `python3 -m camayoc_cli`: the agent is not held while the worker runs."""
        payload = json.dumps(write(self.box.path("push-policy.md"), fixture("push-policy.md")))
        env = {**os.environ, **self.env, "PYTHONPATH": str(ROOT)}
        start = time.monotonic()
        proc = subprocess.run([sys.executable, "-m", "camayoc_cli", "hook", "memory-redirect", "--mode", "copy"],
                              input=payload, capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=20)
        self.assertEqual((0, "", ""), (proc.returncode, proc.stdout, proc.stderr))
        self.assertLess(time.monotonic() - start, 5)
        Path(self.box.path("push-policy.md")).write_text(fixture("push-policy.md"))  # the harness's write lands
        for _ in range(100):
            if any(r["event"] == "queued" for r in self.box.log()):
                break
            time.sleep(0.1)
        self.assertEqual(["fired", "queued"], [r["event"] for r in self.box.log()])


@requires_quipu_server
class LocalStoreRedirectTests(unittest.TestCase):
    """Copy mode against a throwaway local quipu-server."""

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        port = free_port()
        cls.base = f"http://127.0.0.1:{port}"
        cls.proc = subprocess.Popen([QUIPU_SERVER_BIN, "--db", str(Path(cls.temp.name) / "store.db"),
                                     "--bind", f"127.0.0.1:{port}"], cwd=cls.temp.name,
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
        seed = "".join(f"<{A}{n}> <http://www.w3.org/2000/01/rdf-schema#label> \"{n}\" .\n"
                       for n in ("repo-goldblum", "hammond"))
        seed += f"<{A}person-stiwi> a <{A}Person> ; <http://www.w3.org/2000/01/rdf-schema#label> \"Stiwi\" .\n"
        cls.post("/knot", {"turtle": seed, "actor": "test"})

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(timeout=5)
        cls.temp.cleanup()

    @classmethod
    def post(cls, path, payload=None):
        req = urllib.request.Request(cls.base + path, data=None if payload is None else json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"},
                                     method="GET" if payload is None else "POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.load(resp)

    def setUp(self):
        self.box = Sandbox()
        self.env = {**self.box.env, "QUIPU_SERVER": self.base}

    def tearDown(self):
        self.box.close()

    def copy(self, name: str, text: str | None = None) -> dict:
        content = text if text is not None else fixture(name)
        payload = write(self.box.path(name), content)
        Path(self.box.path(name)).write_text(content)
        with mock.patch.dict(os.environ, self.env):
            return mr.run_job(mr.spool(mr.targets(payload)[0], person="person-stiwi", env=self.env), self.env)

    def test_copy_records_typed_entities_and_refuses_in_plain_words(self):
        with mock.patch.dict(os.environ, self.env):
            for topic in ("push-policy", "deploy-window"):
                self.assertEqual(0, memory.add_topic(topic, [], env=self.env).code)
        got = self.copy("push-policy.md")
        self.assertEqual(("recorded", "directive"), (got["event"], got["entity"]), got)
        self.assertRegex(got["lines"][0], rf"^recorded directive {A}mem/directive/repo-goldblum/push-policy/")
        got = self.copy("deploy-window.md")
        self.assertEqual("recorded", got["event"], got)
        with mock.patch.dict(os.environ, self.env):
            rows = memory.recall("directive", {"appliesTo": "hammond"})
        self.assertIn("deploy-window", json.dumps(rows.data))
        # an unregistered topic is refused with the registration call, and nothing queues
        got = self.copy("chat-signoff.md")
        self.assertEqual("refused", got["event"])
        self.assertIn("cy topic add", " ".join(got["lines"]))
        self.assertEqual(0, memory.queue_length(self.env))
        # a secret never reaches the store or the log
        text, token = secret_memory()
        got = self.copy("pane-read.md", text)
        self.assertEqual("refused", got["event"])
        self.assertNotIn(token, mr.log_path(self.env).read_text())
        found = self.post("/query", {"query": f"SELECT ?s WHERE {{ GRAPH ?g {{ ?s ?p ?o FILTER(CONTAINS(STR(?o), "
                                              f"\"{token[:12]}\")) }} }}"})
        self.assertEqual([], found.get("rows") or found.get("results", {}).get("bindings") or [])


if __name__ == "__main__":
    unittest.main()
