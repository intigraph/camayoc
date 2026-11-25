"""Alertmanager receiver, spool and drain (aegis-dc5 G1). Offline: a loopback
server and a stub writer, no Quipu and no credentials."""
from __future__ import annotations

import http.client
import json
import os
import stat
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import alertmanager_receiver as rx  # noqa: E402
from test_alertmanager_ingress import FIRING, notification, resolved  # noqa: E402

TOKEN = b"test-token-not-a-secret"


class Receiver(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.spool = rx.Spool(os.path.join(self.dir.name, "spool.db"))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), rx.handler_for(self.spool, TOKEN, "camayoc-test"))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.spool.conn.close()
        self.dir.cleanup()

    def post(self, body: object, *, token: bytes | None = TOKEN, path: str = rx.PATH,
             raw: bytes | None = None, length: str | None = None) -> tuple[int, dict]:
        conn = http.client.HTTPConnection(*self.server.server_address, timeout=10)
        data = raw if raw is not None else json.dumps(body).encode()
        headers = {"Content-Type": "application/json", "Content-Length": length or str(len(data))}
        if token is not None:
            headers["Authorization"] = "Bearer " + token.decode()
        conn.request("POST", path, body=data, headers=headers)
        resp = conn.getresponse()
        out = json.loads(resp.read() or b"{}")
        conn.close()
        return resp.status, out

    def test_spool_file_is_owner_only(self) -> None:
        mode = stat.S_IMODE(os.stat(os.path.join(self.dir.name, "spool.db")).st_mode)
        self.assertEqual(mode, 0o600)

    def test_valid_notification_is_spooled_mapped_not_raw(self) -> None:
        code, out = self.post(notification())
        self.assertEqual((code, out["outcome"], out["new"]), (200, "spooled", 1))
        rows = self.spool.conn.execute("SELECT body FROM records").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertNotIn("never stored", rows[0][0])  # annotation
        self.assertNotIn("prometheus.example.invalid", rows[0][0])  # generatorURL

    def test_repeat_send_is_one_record(self) -> None:
        self.post(notification())
        code, out = self.post(notification(dict(FIRING, endsAt="2026-11-25T04:30:00Z"), groupKey="g2"))
        self.assertEqual((code, out["new"]), (200, 0))
        self.assertEqual(self.spool.status()["pending"], 1)

    def test_resolution_is_a_second_record(self) -> None:
        self.post(notification())
        self.post(notification(resolved()))
        self.assertEqual(self.spool.status()["pending"], 2)

    def test_bad_or_missing_token_is_refused_and_spools_nothing(self) -> None:
        for token in (None, b"wrong", TOKEN + b"x"):
            with self.subTest(token=token):
                self.assertEqual(self.post(notification(), token=token)[0], 401)
        self.assertEqual(self.spool.status(), {"pending": 0, "written": 0, "parked": 0,
                                               "oldest_pending_received_at": None})

    def test_oversize_and_wrong_path(self) -> None:
        self.assertEqual(self.post(None, raw=b"{}", length=str(rx.MAX_BODY + 1))[0], 413)
        self.assertEqual(self.post(notification(), path="/webhook/alert")[0], 404)

    def test_unmappable_notification_is_parked_and_acknowledged(self) -> None:
        code, out = self.post(notification(version="3"))
        self.assertEqual((code, out["outcome"]), (200, "parked"))
        code, out = self.post(None, raw=b"not json")
        self.assertEqual((code, out["outcome"]), (200, "parked"))
        status = self.spool.status()
        self.assertEqual((status["parked"], status["pending"]), (2, 0))


class Drain(unittest.TestCase):
    def setUp(self) -> None:
        self.spool = rx.Spool(":memory:")
        bodies = rx.ai.episodes_for(notification(), actor="t") + rx.ai.episodes_for(notification(resolved()), actor="t")
        self.spool.add(bodies, now=100.0)
        self.clock = [200.0]

    def test_drain_writes_each_record_once(self) -> None:
        sent: list[str] = []
        result = rx.drain_once(self.spool, post=lambda b: sent.append(b["name"]) or {}, clock=lambda: self.clock[0])
        self.assertEqual((result["sent"], result["failed"], result["written"]), (2, 0, 2))
        rx.drain_once(self.spool, post=lambda b: sent.append(b["name"]) or {}, clock=lambda: self.clock[0])
        self.assertEqual(len(sent), 2)

    def test_failed_post_is_retried_after_backoff_with_identical_body(self) -> None:
        bodies: list[str] = []

        def flaky(body: dict) -> dict:
            bodies.append(json.dumps(body, sort_keys=True))
            if len(bodies) <= 2:
                raise TimeoutError("408")
            return {}

        first = rx.drain_once(self.spool, post=flaky, clock=lambda: self.clock[0])
        self.assertEqual((first["sent"], first["failed"], first["pending"]), (0, 2, 2))
        # Not due again until the backoff elapses.
        self.assertEqual(rx.drain_once(self.spool, post=flaky, clock=lambda: self.clock[0])["sent"], 0)
        self.clock[0] += rx.BACKOFF_MAX
        again = rx.drain_once(self.spool, post=flaky, clock=lambda: self.clock[0])
        self.assertEqual((again["sent"], again["pending"]), (2, 0))
        self.assertEqual(sorted(bodies[:2]), sorted(bodies[2:]))  # byte-identical re-send


if __name__ == "__main__":
    unittest.main()
