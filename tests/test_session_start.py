"""The SessionStart hook must not certify a store nobody configured — aegis-p1twft.

An orphaned test quipu once held 127.0.0.1:3030 for ~5 days. Every
session with no QUIPU_SERVER and no env.json fell back to that default port,
got an answer, and was told "governed memory ACTIVE" — about a test fixture.
quipu's /stats carries no store identity, so the hook cannot tell a project's
store from any other process on the port. What it CAN do is say where the
address came from, and refuse to call an unconfigured responder ACTIVE.

The stub is not a quipu: these tests prove what the hook concludes, given a
responder that claims camayoc shapes are loaded.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "scripts" / "session_start.sh"


class Stub(BaseHTTPRequestHandler):
    def log_message(self, *_a):
        pass

    def _send(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # /stats, /health
        self._send(b'{"entities":12,"facts":34,"predicates":5}')

    def do_POST(self):  # /shapes list
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self._send(b'{"shape_sets":["camayoc-core"]}')


def dead_url() -> str:
    """A loopback port that nothing is listening on."""
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    port = srv.server_address[1]
    srv.server_close()
    return f"http://127.0.0.1:{port}"


class SessionStartTest(unittest.TestCase):
    def setUp(self):
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
        self.port = self.srv.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.project = Path(tempfile.mkdtemp())

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def run_hook(self, **env_over) -> str:
        env = {k: v for k, v in os.environ.items() if k not in ("QUIPU_SERVER", "QUIPU_AUTH_TOKEN", "QUIPU_AUTH_TOKEN_FILE")}
        env["CLAUDE_PROJECT_DIR"] = str(self.project)
        env["XDG_STATE_HOME"] = str(self.project / "state")
        env.update(env_over)
        p = subprocess.run(["bash", str(HOOK)], cwd=self.project, env=env,
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)  # never blocks a session
        return json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"]

    def test_replay_failure_never_blocks_the_existing_hook(self):
        bindir = self.project / "bin"
        bindir.mkdir()
        stub = bindir / "cy"
        stub.write_text("#!/bin/sh\nprintf called > replay-called\nexit 7\n")
        stub.chmod(0o700)
        context = self.run_hook(PATH=str(bindir) + os.pathsep + os.environ["PATH"],
                                QUIPU_SERVER=self.url)
        self.assertIn("ACTIVE", context)
        self.assertEqual("called", (self.project / "replay-called").read_text())

    # -- the incident --------------------------------------------------------
    def test_an_unconfigured_responder_on_the_default_port_is_not_active(self):
        ctx = self.run_hook(CAMAYOC_DEFAULT_QUIPU=self.url)
        self.assertNotIn("ACTIVE", ctx)
        self.assertIn("UNVERIFIED", ctx)
        self.assertIn("NOT established", ctx)
        self.assertIn("nothing configured", ctx)
        self.assertIn(self.url, ctx)

    def test_an_unreachable_default_says_nothing_was_configured(self):
        ctx = self.run_hook(CAMAYOC_DEFAULT_QUIPU=dead_url())
        self.assertIn("could not reach quipu at the DEFAULT", ctx)
        self.assertIn("nothing configured", ctx)

    # -- configured sources still certify, and are named ---------------------
    def test_quipu_server_env_is_active_and_named(self):
        ctx = self.run_hook(QUIPU_SERVER=self.url, CAMAYOC_DEFAULT_QUIPU=dead_url())
        self.assertIn(f"ACTIVE at {self.url} (from QUIPU_SERVER)", ctx)

    def test_env_json_is_active_and_named(self):
        (self.project / "env.json").write_text(json.dumps({"quipu_server": self.url}))
        ctx = self.run_hook(CAMAYOC_DEFAULT_QUIPU=dead_url())
        self.assertIn(f"ACTIVE at {self.url} (from env.json)", ctx)

    def test_bootstrap_config_bind_is_read(self):
        # The shape bootstrap.sh writes. The skill documented this step; the
        # hook skipped it until aegis-p1twft.
        (self.project / ".bobbin").mkdir()
        (self.project / ".bobbin" / "config.toml").write_text(
            '[quipu]\nstore_path = ".quipu/store.db"\n\n'
            '[quipu.shacl]\nvalidate_on_write = true\n\n'
            f'[quipu.server]\nbind = "127.0.0.1:{self.port}"\n')
        ctx = self.run_hook(CAMAYOC_DEFAULT_QUIPU=dead_url())
        self.assertIn(f"ACTIVE at {self.url} (from .bobbin/config.toml)", ctx)

    def test_a_bind_outside_quipu_server_is_ignored(self):
        (self.project / ".bobbin").mkdir()
        (self.project / ".bobbin" / "config.toml").write_text(
            f'[other.server]\nbind = "127.0.0.1:{self.port}"\n')
        ctx = self.run_hook(CAMAYOC_DEFAULT_QUIPU=dead_url())
        self.assertIn("could not reach quipu at the DEFAULT", ctx)

    # -- precedence: env > env.json > config.toml > default -----------------
    def test_precedence(self):
        dead = dead_url()
        (self.project / ".bobbin").mkdir()
        (self.project / ".bobbin" / "config.toml").write_text(
            f'[quipu.server]\nbind = "{dead.removeprefix("http://")}"\n')
        ctx = self.run_hook(CAMAYOC_DEFAULT_QUIPU=self.url)
        self.assertIn("from .bobbin/config.toml", ctx)  # config beats default

        (self.project / "env.json").write_text(json.dumps({"quipu_server": self.url}))
        ctx = self.run_hook(CAMAYOC_DEFAULT_QUIPU=dead)
        self.assertIn("from env.json", ctx)  # env.json beats config

        ctx = self.run_hook(QUIPU_SERVER=dead, CAMAYOC_DEFAULT_QUIPU=self.url)
        self.assertIn("from QUIPU_SERVER", ctx)  # env beats everything
        self.assertIn("could not reach", ctx)


if __name__ == "__main__":
    unittest.main()
