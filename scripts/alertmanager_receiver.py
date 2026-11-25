#!/usr/bin/env python3
"""Alertmanager webhook receiver, spool and drain (aegis-dc5 G1).

    alertmanager_receiver.py serve  --token-file F --spool DB --actor A [--bind H] [--port P]
    alertmanager_receiver.py drain  --spool DB [--once | --loop SECONDS]
    alertmanager_receiver.py status --spool DB

``serve`` accepts ``POST /alertmanager`` from Alertmanager's webhook_configs
with a bearer it compares in constant time (Alertmanager sends it from
``http_config.authorization.credentials_file``). Each notification is mapped
at once by ``alertmanager_ingress.episodes_for`` and only the MAPPED records
are spooled: raw labels, annotations and URLs never touch disk. A record is
keyed by its episode name (a digest of the transition), so a repeat-interval
re-send is ``INSERT OR IGNORE``: one row, written once.

A notification that cannot be mapped is parked in the spool with its reason
and answered 200, because Alertmanager retries non-2xx forever and a payload
that cannot map will never map on retry. Bad auth is 401, oversize 413.

``drain`` posts due records through Camayoc's ingress helper. A failed post
is retried with backoff. Every record is byte-identical on re-send, so a
retry after an unknown outcome writes the same triples again rather than a
second fact: retry is safe by construction, not by luck.

The receiver needs no Quipu credential; the drain does. They may run as
different users sharing a group spool (``--spool-group-shared``).
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import sqlite3
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

import alertmanager_ingress as ai
from github_ingress import _secure_spool_file
from ingest_cdevents import Quarantined

MAX_BODY = 1024 * 1024
PATH = "/alertmanager"
BACKOFF_MAX = 900.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
  name TEXT PRIMARY KEY, body TEXT NOT NULL, received_at REAL NOT NULL,
  state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
  next_at REAL NOT NULL DEFAULT 0, reason TEXT, written_at REAL);
CREATE INDEX IF NOT EXISTS records_due ON records (state, next_at);
CREATE TABLE IF NOT EXISTS parked (
  id INTEGER PRIMARY KEY AUTOINCREMENT, received_at REAL NOT NULL, reason TEXT NOT NULL);
"""


class Spool:
    """Mapped alert records awaiting a write. Thread-safe by a lock."""

    def __init__(self, path: str, *, group_shared: bool = False):
        mode = 0o660 if group_shared else 0o600
        old = os.umask(0o007 if group_shared else 0o077)
        try:
            if path != ":memory:":
                try:
                    os.close(os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, mode))
                except OSError as exc:
                    if os.path.islink(path):
                        raise PermissionError(f"spool path is not a regular file: {path}") from exc
                    raise
            self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=10)
            self.lock = threading.Lock()
            with self.lock:
                if path != ":memory:":
                    self.conn.execute("PRAGMA journal_mode=WAL")
                self.conn.execute("PRAGMA synchronous=FULL")
                self.conn.executescript(SCHEMA)
        finally:
            os.umask(old)
        if path != ":memory:":
            try:
                for suffix in ("", "-wal", "-shm"):
                    _secure_spool_file(path + suffix, mode, group_shared)
            except BaseException:
                self.conn.close()
                raise

    def add(self, bodies: list[dict], now: float) -> int:
        """Spool mapped records atomically; returns how many were new."""
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                new = 0
                for body in bodies:
                    cur = self.conn.execute(
                        "INSERT OR IGNORE INTO records (name, body, received_at) VALUES (?,?,?)",
                        (body["name"], json.dumps(body, sort_keys=True), now))
                    new += cur.rowcount
                self.conn.execute("COMMIT")
                return new
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise

    def park(self, reason: str, now: float) -> None:
        with self.lock:
            self.conn.execute("INSERT INTO parked (received_at, reason) VALUES (?,?)", (now, reason[:500]))

    def due(self, now: float, limit: int = 50) -> list[tuple[str, str, int]]:
        with self.lock:
            return self.conn.execute(
                "SELECT name, body, attempts FROM records WHERE state='pending' AND next_at<=? "
                "ORDER BY received_at, name LIMIT ?", (now, limit)).fetchall()

    def written(self, name: str, now: float) -> None:
        with self.lock:
            self.conn.execute("UPDATE records SET state='written', reason=NULL, written_at=? WHERE name=?",
                              (now, name))

    def retry(self, name: str, attempts: int, reason: str, now: float) -> None:
        delay = min(BACKOFF_MAX, 5.0 * (2 ** min(attempts, 10)))
        with self.lock:
            self.conn.execute("UPDATE records SET attempts=?, next_at=?, reason=? WHERE name=?",
                              (attempts + 1, now + delay, reason[:500], name))

    def status(self) -> dict:
        with self.lock:
            counts = dict(self.conn.execute("SELECT state, COUNT(*) FROM records GROUP BY state").fetchall())
            parked = self.conn.execute("SELECT COUNT(*) FROM parked").fetchone()[0]
            oldest = self.conn.execute(
                "SELECT MIN(received_at) FROM records WHERE state='pending'").fetchone()[0]
        return {"pending": counts.get("pending", 0), "written": counts.get("written", 0),
                "parked": parked, "oldest_pending_received_at": oldest}


def read_token(path: Path) -> bytes:
    token = path.read_bytes().strip()
    if not token:
        raise SystemExit(f"refused: empty token in {path}")
    return token


def handler_for(spool: Spool, token: bytes, actor: str, clock: Callable[[], float] = time.time):
    class Handler(BaseHTTPRequestHandler):
        server_version = "camayoc-alertmanager-ingress"

        def log_message(self, fmt: str, *args: object) -> None:  # no request lines with headers
            pass

        def reply(self, code: int, payload: dict) -> None:
            data = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/health":
                self.reply(200, {"ok": True})
            else:
                self.reply(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            if self.path != PATH:
                self.reply(404, {"error": "not found"})
                return
            given = self.headers.get("Authorization", "")
            expected = b"Bearer " + token
            if not hmac.compare_digest(given.encode(), expected):
                self.reply(401, {"error": "unauthorized"})
                return
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                self.reply(411, {"error": "length required"})
                return
            if length < 0 or length > MAX_BODY:
                self.reply(413, {"error": "too large"})
                return
            raw = self.rfile.read(length)
            now = clock()
            try:
                bodies = ai.episodes_for(json.loads(raw), actor=actor)
            except (Quarantined, ValueError) as exc:
                spool.park(str(exc), now)
                self.reply(200, {"outcome": "parked", "reason": str(exc)})
                return
            new = spool.add(bodies, now)
            self.reply(200, {"outcome": "spooled", "records": len(bodies), "new": new})

    return Handler


def post_episode(body: dict) -> dict:
    """The real writer: ensure the window, then the governed /episode."""
    import planes
    import windows

    family, month = body["graph"][len(windows.WINDOW_NS):].rsplit("/", 1)
    windows.ensure_window(family, month, body["nodes"][1]["properties"]["occurredAt"])
    return planes._post("/episode", body, client="camayoc-ingress")


def drain_once(spool: Spool, post: Callable[[dict], dict] = post_episode,
               clock: Callable[[], float] = time.time) -> dict:
    sent = failed = 0
    for name, body, attempts in spool.due(clock()):
        try:
            post(json.loads(body))
        except Exception as exc:  # retried: the record is byte-identical on re-send
            spool.retry(name, attempts, f"{type(exc).__name__}: {exc}", clock())
            failed += 1
            continue
        spool.written(name, clock())
        sent += 1
    return {"sent": sent, "failed": failed, **spool.status()}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("serve", "drain", "status"):
        p = sub.add_parser(name)
        p.add_argument("--spool", required=True)
        p.add_argument("--spool-group-shared", action="store_true")
        if name == "serve":
            p.add_argument("--token-file", type=Path, required=True)
            p.add_argument("--actor", required=True)
            p.add_argument("--bind", default="127.0.0.1")
            p.add_argument("--port", type=int, default=5061)
        if name == "drain":
            mode = p.add_mutually_exclusive_group(required=True)
            mode.add_argument("--once", action="store_true")
            mode.add_argument("--loop", type=float, metavar="SECONDS")
    args = ap.parse_args(argv)
    spool = Spool(args.spool, group_shared=args.spool_group_shared)
    if args.cmd == "status":
        print(json.dumps(spool.status(), sort_keys=True))
        return 0
    if args.cmd == "serve":
        token = read_token(args.token_file)
        server = ThreadingHTTPServer((args.bind, args.port), handler_for(spool, token, args.actor))
        server.serve_forever()
        return 0
    while True:
        result = drain_once(spool)
        print(json.dumps(result, sort_keys=True), flush=True)
        if args.once:
            return 0 if result["failed"] == 0 else 1
        time.sleep(args.loop)


if __name__ == "__main__":
    sys.exit(main())
