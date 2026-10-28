"""A small stdlib Quipu REST client for cy.

Credentials resolve exactly as scripts/quipu_auth.py does:
QUIPU_AUTH_TOKEN > QUIPU_AUTH_TOKEN_FILE > ~/.config/quipu/token. The bearer is
sent only in the Authorization header and never printed, logged or echoed in
an error. Writes carry the stack's X-Quipu-* provenance headers.
"""
from __future__ import annotations

import json
import os
import re
import socket
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_SERVER = "http://localhost:3030"
ROOT_GRAPH = "urn:quipu:graph:root"
READ_PATHS = {"/query", "/ask", "/resolve", "/health"}
_UNSAFE = re.compile(r"[^\x21-\x7e ]")


class QuipuError(RuntimeError):
    """A request failed for a reason other than SHACL refusal."""


class Unreachable(QuipuError):
    """Could not connect: not evidence about the store's contents."""


class TimedOut(Unreachable):
    """Connected (or tried) but no answer inside the deadline."""


class Refused(QuipuError):
    """The store answered and refused the write (SHACL or vocabulary)."""

    def __init__(self, messages: list[str], issues: list[dict] | None = None):
        super().__init__("; ".join(messages))
        self.messages = messages
        self.issues = issues or []


def server() -> str:
    return os.environ.get("QUIPU_SERVER", DEFAULT_SERVER).rstrip("/")


def token() -> str:
    value = os.environ.get("QUIPU_AUTH_TOKEN", "").strip()
    if not value:
        path = os.environ.get("QUIPU_AUTH_TOKEN_FILE") or str(Path.home() / ".config/quipu/token")
        try:
            value = Path(path).read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            return ""
    if value and not re.fullmatch(r"[A-Za-z0-9._~+/=-]+", value):
        raise QuipuError("the Quipu credential is unreadable or malformed; "
                         "configure QUIPU_AUTH_TOKEN, QUIPU_AUTH_TOKEN_FILE or ~/.config/quipu/token")
    return value


def has_token() -> bool:
    try:
        return bool(token())
    except QuipuError:
        return False


def provenance_headers(env=None) -> dict[str, str]:
    env = os.environ if env is None else env
    harness = env.get("QUIPU_HARNESS") or (
        "claude" if env.get("CLAUDECODE") == "1" else "codex" if env.get("CODEX_HOME") else "cli")
    raw = {
        "Agent": env.get("QUIPU_AGENT") or env.get("SHANTY_AGENT") or "camayoc-cy",
        "Harness": harness,
        "Model": env.get("QUIPU_MODEL") or env.get("SHANTY_MODEL"),
        "Session": env.get("QUIPU_SESSION") or env.get("CLAUDE_CODE_SESSION_ID")
        or env.get("CODEX_SESSION_ID") or env.get("CODEX_THREAD_ID"),
        "Host": env.get("QUIPU_HOST") or socket.gethostname(),
    }
    out = {}
    for field, value in raw.items():
        text = _UNSAFE.sub("", str(value or "")).strip()[:128]
        if text:
            out[f"X-Quipu-{field}"] = text
    return out


class Client:
    def __init__(self, base: str | None = None, timeout: float = 15.0):
        self.base = (base or server()).rstrip("/")
        self.timeout = timeout

    def _request(self, path: str, body: dict | None = None, timeout: float | None = None) -> dict:
        headers = {"Content-Type": "application/json", "X-Quipu-Client": "camayoc-cy", **provenance_headers()}
        bearer = token()
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, headers=headers,
                                     method="GET" if body is None else "POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                return json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:4000].decode(errors="replace")
            exc.close()
            if exc.code == 401:
                raise QuipuError("Quipu refused the credential (HTTP 401); configure QUIPU_AUTH_TOKEN, "
                                 "QUIPU_AUTH_TOKEN_FILE or ~/.config/quipu/token with an accepted one") from None
            try:
                parsed = json.loads(detail)
            except ValueError:
                parsed = {"error": detail}
            if exc.code == 400 and path == "/knot" and "unknown rdf:type" in str(parsed.get("error", "")):
                raise Refused([f"Not recorded: this store has no agent-memory shapes loaded ({parsed['error'][:160]})."]) from None
            if "unknown graph" in str(parsed.get("error", "")):
                raise QuipuError("the camayoc plane graphs are not registered on this store; register and label "
                                 "them with `python3 scripts/planes.py ensure` (camayoc repo) first") from None
            raise QuipuError(f"{path} failed: HTTP {exc.code} {str(parsed.get('error', detail))[:300]}") from None
        except (socket.timeout, TimeoutError):
            raise TimedOut(f"Quipu at {self.base} gave no answer in time") from None
        except (urllib.error.URLError, ConnectionError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise TimedOut(f"Quipu at {self.base} gave no answer in time") from None
            raise Unreachable(f"Quipu unreachable at {self.base}: {reason}") from None

    def health(self, timeout: float | None = None) -> dict:
        return self._request("/health", timeout=timeout)

    def query(self, sparql: str, timeout: float | None = None) -> list[dict]:
        return self._request("/query", {"query": sparql, "verbose": True}, timeout=timeout).get("rows", [])

    def ask(self, name: str, params: dict) -> dict:
        return self._request("/ask", {"name": name, "params": params})

    def resolve(self, name: str, top_k: int = 3) -> list[dict]:
        try:
            return self._request("/resolve", {"name": name, "top_k": top_k}).get("candidates", [])
        except QuipuError:
            return []

    def knot(self, turtle: str, graph: str | None, actor: str, source: str) -> dict:
        body = {"turtle": turtle, "actor": actor, "source": source}
        if graph:
            body["graph"] = graph
        out = self._request("/knot", body)
        if out.get("conforms") is False:
            issues = out.get("issues") or []
            messages = []
            for issue in issues:
                msg = issue.get("message") or f"{issue.get('path')} violates {issue.get('component')}"
                if msg not in messages:
                    messages.append(msg)
            raise Refused(messages or ["Not recorded: the store refused the write."], issues)
        return out


def value(cell):
    """A /query cell as a plain string (typed literals arrive as objects)."""
    if isinstance(cell, dict):
        return str(cell.get("value", ""))
    return "" if cell is None else str(cell)
