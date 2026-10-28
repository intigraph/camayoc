#!/usr/bin/env python3
"""GitHub activity -> governed Quipu episodes: one ingress (aegis-c01c.3).

Design: docs/design/github-ingress.md. Shapes: shapes/github.shapes.ttl.
Admission data: mappings/github.json (its sha256 rides in every source).

    github_ingress.py map EVENT FILE --actor A          one payload -> /episode body
    github_ingress.py replay DIR --actor A [--post]     recorded deliveries, end to end
    github_ingress.py serve --secret-file F --spool DB --repo O/N ... --actor A
    github_ingress.py poll  --spool DB --repo O/N ... --actor A [--once]
    github_ingress.py drain --spool DB --actor A [--post]
    github_ingress.py status --spool DB

Every transport puts deliveries into ONE SQLite spool and one drain maps them,
so the webhook receiver and the budgeted poller cannot disagree about meaning:

- receiver: HMAC-SHA256 (X-Hub-Signature-256) over the exact body, checked in
  constant time BEFORE any JSON parse; an empty or missing secret refuses
  everything. Spool first, then 202; a known X-GitHub-Delivery answers 200.
- poller: only through `gh` (it holds the credential; the aegis-izs shim meters
  it), ETag conditional reads, an hourly budget and a rate-limit reserve. A
  403/429 ends the cycle and persists a backoff; a failed object fetch stays
  retryable in the spool (state `hydrate`), never quarantined unless 404/410.
- spool: REDACTED before it touches disk (no commits, pusher, emails or text;
  a body becomes its @-mentions), mode 0600, kept --retain-days (default 2).
- drain: payload -> episode in the ONE `github` operational graph. The activity key (payload fields only) is
  recorded in the spool, so the same change from both transports is written
  once. A re-sent body is byte-identical, so a lost Quipu response is safe to
  retry. An event the mapping cannot place is QUARANTINED with its reason.

Only identifiers, times, verdict words and @-mentions leave the payload:
never comment, review or PR bodies, commit messages or logs.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ingest_cdevents import Quarantined, event_month

ROOT = Path(__file__).resolve().parents[1]
MAPPING_FILE = ROOT / "mappings" / "github.json"
GRAPH_NAME = "github"
RETAIN_DAYS = 2  # long enough to replay a remap or a Quipu outage; no longer
MAX_PENDING = 10000  # above this the receiver answers 503 instead of filling the disk
GROUP = "github"
CLIENT = "camayoc-github-ingress"
MAX_BODY = 5 * 1024 * 1024
API = "https://api.github.com"
ZERO_SHA = "0" * 40

_SHA = re.compile(r"^[0-9a-f]{40}$")
_NODE = re.compile(r"^[A-Za-z0-9_-]+$")
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_FENCE = re.compile(r"```.*?```|`[^`\n]*`", re.S)
_MENTION = re.compile(r"(?<![A-Za-z0-9_`/.@-])@([A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38})(?![A-Za-z0-9_/-])")
_REVIEW_STATES = {"approved", "changes_requested", "commented", "dismissed"}
_STATUSES = {"queued", "in_progress", "completed", "requested", "waiting", "pending"}
_CONCLUSIONS = {"success", "failure", "neutral", "cancelled", "skipped", "timed_out",
                "action_required", "stale", "startup_failure"}


class Ignored(Exception):
    """A delivery that is acknowledged and writes nothing (ping)."""


class MissingField(Quarantined):
    """A required field is absent; `hydrate` names the object's API URL, if any."""

    def __init__(self, message: str, hydrate: str | None = None):
        super().__init__(message)
        self.hydrate = hydrate


# ---- mapping ----------------------------------------------------------------

def load_mapping(path: Path = MAPPING_FILE) -> tuple[dict, str]:
    raw = path.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def admitted_kinds(mapping: dict) -> set[str]:
    kinds = set()
    for event, rule in mapping["events"].items():
        if rule["actions"] is None:
            kinds.add(event)
        else:
            kinds.update(f"{event}.{a}" for a in rule["actions"])
    return kinds


def node_name(node_id: str) -> str:
    """Entity node name for a GitHub node id. Ids outside [A-Za-z0-9_-] (legacy
    base64) are hashed, never sanitized: sanitizing could merge two ids."""
    if _NODE.match(node_id):
        return f"github.{node_id}"
    return "github.b" + hashlib.sha256(node_id.encode()).hexdigest()[:32]


def _digest(*parts: object) -> str:
    return hashlib.sha256("\0".join(str(p) for p in parts).encode()).hexdigest()


def _need(obj: object, key: str, where: str, kind: type | tuple = str) -> object:
    value = obj.get(key) if isinstance(obj, dict) else None
    if value is None or (isinstance(value, str) and not value.strip()) or not isinstance(value, kind) \
            or isinstance(value, bool) and kind is not bool:
        hydrate = obj.get("url") if isinstance(obj, dict) and isinstance(obj.get("url"), str) else None
        raise MissingField(f"required field {where}.{key} absent or malformed", hydrate)
    return value


def _opt(obj: object, key: str, kind: type | tuple = str) -> object:
    value = obj.get(key) if isinstance(obj, dict) else None
    if isinstance(value, bool) and kind is not bool:
        return None
    return value if isinstance(value, kind) and value != "" else None


def _login(obj: object) -> str | None:
    return _opt(obj, "login") if isinstance(obj, dict) else None


def _instant(value: object, where: str) -> str:
    """RFC 3339 from the payload (epoch seconds accepted for push), validated."""
    if isinstance(value, int) and not isinstance(value, bool):
        value = datetime.fromtimestamp(value, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if not isinstance(value, str):
        raise MissingField(f"time {where} absent")
    event_month(value)
    return value


def mentions(*texts: object) -> list[str]:
    """Logins @-mentioned outside code, lower-cased, sorted. The text is discarded."""
    found = set()
    for text in texts:
        if isinstance(text, str):
            found.update(m.lower() for m in _MENTION.findall(_FENCE.sub(" ", text)))
    return sorted(found)


def _sha(value: object) -> str | None:
    return value if isinstance(value, str) and _SHA.match(value) else None


def _repository(payload: dict) -> dict:
    repo = payload.get("repository")
    full_name = _need(repo, "full_name", "repository")
    if not _REPO.match(full_name):
        raise Quarantined(f"repository.full_name {full_name!r} is not owner/name")
    node_id = _need(repo, "node_id", "repository")
    props = {"githubNodeId": node_id, "githubFullName": full_name}
    if _opt(repo, "id", int) is not None:
        props["githubDatabaseId"] = repo["id"]
    if _opt(repo, "html_url"):
        props["githubUrl"] = repo["html_url"]
    return {"name": node_name(node_id), "type": "GitHubRepository", "description": full_name,
            "properties": props}


def _entity(cls: str, obj: dict, where: str, repo: dict, *, number: bool = False,
            extra: dict | None = None, label: str | None = None) -> dict:
    node_id = _need(obj, "node_id", where)
    props: dict = {"githubNodeId": node_id}
    if number:
        props["githubNumber"] = _need(obj, "number", where, int)
    if _opt(obj, "id", int) is not None:
        props["githubDatabaseId"] = obj["id"]
    if _opt(obj, "html_url"):
        props["githubUrl"] = obj["html_url"]
    author = _login(obj.get("user") or obj.get("author"))
    if author and cls in ("GitHubPullRequest", "GitHubIssue", "GitHubComment", "GitHubReview"):
        props["githubAuthorLogin"] = author
    if cls in ("GitHubPullRequest", "GitHubIssue") and _opt(obj, "created_at"):
        props["githubCreatedAt"] = _instant(obj["created_at"], f"{where}.created_at")
    props.update(extra or {})
    return {"name": node_name(node_id), "type": cls,
            "description": label or f"{repo['properties']['githubFullName']} {where} {node_id}",
            "properties": props}


def _thread(payload: dict, key: str, repo: dict) -> dict:
    obj = payload.get(key)
    is_pr = key == "pull_request" or (isinstance(obj, dict) and "pull_request" in obj)
    cls = "GitHubPullRequest" if is_pr else "GitHubIssue"
    full = repo["properties"]["githubFullName"]
    number = _need(obj, "number", key, int)
    return _entity(cls, obj, key, repo, number=True, label=f"{full}#{number}")


def episode_for(event: str, payload: object, *, actor: str, occurred_at: str | None = None,
                mapping: dict | None = None, mapping_sha: str | None = None) -> tuple[dict, str]:
    """(the governed /episode body, the activity key digest) for one delivery.

    Raises Ignored (ping), Quarantined (unmapped, malformed) or MissingField
    (a Quarantined that names the object to hydrate). `occurred_at` overrides
    the payload's own time, for the poller (Events API created_at)."""
    if mapping is None or mapping_sha is None:
        mapping, mapping_sha = load_mapping()
    if event == "ping":
        raise Ignored("ping")
    if not isinstance(payload, dict):
        raise Quarantined("payload is not a JSON object")
    rule = mapping["events"].get(event)
    if rule is None:
        raise Quarantined(f"no mapping for GitHub event {event!r}")
    action = payload.get("action")
    if rule["actions"] is None:
        kind = event
    elif action in rule["actions"]:
        kind = f"{event}.{action}"
    else:
        raise Quarantined(f"action {action!r} of {event!r} is not mapped")

    repo = _repository(payload)
    full = repo["properties"]["githubFullName"]
    nodes = [repo]
    edges: list[dict] = []
    state: dict = {}
    texts: list[object] = []

    if event == "push":
        ref = _need(payload, "ref", "push")
        before = _sha(payload.get("before"))
        after = _sha(payload.get("after"))
        if not ref.startswith("refs/") or not before or not after:
            raise Quarantined("push needs refs/... and 40-hex before/after")
        subject = {"name": "github.push." + _digest(repo["properties"]["githubNodeId"], ref, before, after)[:32],
                   "type": "GitHubPush", "description": f"{full} {ref} {before[:12]}..{after[:12]}",
                   "properties": {"githubRef": ref, "githubBeforeSha": before, "githubAfterSha": after}}
        if isinstance(payload.get("forced"), bool):
            subject["properties"]["githubForced"] = payload["forced"]
        key = _digest(kind, repo["properties"]["githubNodeId"], ref, before, after)
        when = (payload.get("repository") or {}).get("pushed_at")
        if after != ZERO_SHA:
            state["commitSha"] = after
    else:
        obj = payload.get(rule["object"])
        if not isinstance(obj, dict):
            raise MissingField(f"payload has no {rule['object']} object")
        cls = rule["class"]
        node_id = _need(obj, "node_id", rule["object"])
        if event == "pull_request":
            subject = _thread(payload, "pull_request", repo)
            head = _need(_need(obj, "head", "pull_request", dict), "sha", "pull_request.head")
            base = _need(_need(obj, "base", "pull_request", dict), "ref", "pull_request.base")
            st = _need(obj, "state", "pull_request")
            if st not in ("open", "closed"):
                raise Quarantined(f"pull_request.state {st!r} is not open/closed")
            merged = obj.get("merged")
            if not isinstance(merged, bool):
                merged = obj.get("merged_at") is not None
            state.update(githubState=st, githubMerged=merged, githubBaseRef=base)
            if _sha(head):
                state["commitSha"] = head
            if isinstance(obj.get("draft"), bool):
                state["githubDraft"] = obj["draft"]
            if merged:
                mc = _sha(obj.get("merge_commit_sha"))
                if not mc:
                    raise MissingField("merged pull request without merge_commit_sha", obj.get("url"))
                state["githubMergeCommitSha"] = mc
            if action in ("opened", "edited"):
                texts.append(obj.get("body"))
            when = {"opened": obj.get("created_at"), "closed": obj.get("closed_at")}.get(action, obj.get("updated_at"))
            key = _digest(kind, node_id, {"opened": "", "closed": obj.get("closed_at"),
                                          "synchronize": head}.get(action, obj.get("updated_at")))
        elif event == "issues":
            subject = _thread(payload, "issue", repo)
            st = _need(obj, "state", "issue")
            if st not in ("open", "closed"):
                raise Quarantined(f"issue.state {st!r} is not open/closed")
            state["githubState"] = st
            if action in ("opened", "edited"):
                texts.append(obj.get("body"))
            when = {"opened": obj.get("created_at"), "closed": obj.get("closed_at")}.get(action, obj.get("updated_at"))
            key = _digest(kind, node_id, {"opened": "", "closed": obj.get("closed_at")}.get(action, obj.get("updated_at")))
        elif event in ("pull_request_review", "pull_request_review_comment", "issue_comment"):
            thread = _thread(payload, rule["thread"], repo)
            nodes.append(thread)
            subject = _entity(cls, obj, rule["object"], repo)
            edges.append({"source": subject["name"], "target": thread["name"], "relation": "onGitHubThread"})
            texts.append(obj.get("body"))
            if event == "pull_request_review":
                verdict = str(_need(obj, "state", "review")).lower()
                if verdict not in _REVIEW_STATES:
                    raise Quarantined(f"review.state {verdict!r} is not mapped")
                state["reviewState"] = verdict
                if _sha(obj.get("commit_id")):
                    state["commitSha"] = obj["commit_id"]
                pr_time = (payload.get("pull_request") or {}).get("updated_at")
                when = obj.get("submitted_at") if action == "submitted" else (obj.get("updated_at") or pr_time)
                key = _digest(kind, node_id, "" if action in ("submitted", "dismissed") else obj.get("submitted_at"))
            else:
                if _sha(obj.get("commit_id")):
                    state["commitSha"] = obj["commit_id"]
                when = obj.get("created_at") if action == "created" else obj.get("updated_at")
                key = _digest(kind, node_id, "" if action == "created" else obj.get("updated_at"))
        elif event in ("check_run", "workflow_run"):
            head = _sha(_need(obj, "head_sha", rule["object"]))
            if not head:
                raise Quarantined(f"{rule['object']}.head_sha is not 40-hex")
            subject = _entity(cls, obj, rule["object"], repo, extra={
                "githubName": _need(obj, "name", rule["object"]), "commitSha": head},
                label=f"{full} {obj.get('name')} {node_id}")
            status = _need(obj, "status", rule["object"])
            if status not in _STATUSES:
                raise Quarantined(f"{rule['object']}.status {status!r} is not mapped")
            conclusion = obj.get("conclusion")
            if conclusion is not None and conclusion not in _CONCLUSIONS:
                raise Quarantined(f"{rule['object']}.conclusion {conclusion!r} is not mapped")
            state.update(githubStatus=status, commitSha=head)
            if conclusion:
                state["githubConclusion"] = conclusion
            if event == "workflow_run":
                attempt = _need(obj, "run_attempt", "workflow_run", int)
                state["githubRunAttempt"] = attempt
                branch = _opt(obj, "head_branch")
                when = {"requested": obj.get("created_at"),
                        "in_progress": obj.get("run_started_at") or obj.get("updated_at")}.get(action, obj.get("updated_at"))
                key = _digest(kind, node_id, attempt, status, conclusion)
            else:
                branch = _opt(obj.get("check_suite") or {}, "head_branch")
                when = obj.get("completed_at") if action == "completed" else obj.get("started_at")
                key = _digest(kind, node_id, status, conclusion)
            if branch:
                state["githubBaseRef"] = branch
        elif event == "release":
            subject = _entity(cls, obj, "release", repo, extra={"githubTagName": _need(obj, "tag_name", "release")},
                              label=f"{full} {obj.get('tag_name')}")
            if isinstance(obj.get("prerelease"), bool):
                state["githubPrerelease"] = obj["prerelease"]
            when = obj.get("published_at") or obj.get("created_at")
            key = _digest(kind, node_id)
        else:  # admitted in data but not implemented in code: refuse, never guess
            raise Quarantined(f"event {event!r} is mapped but has no record builder")

    occurred = _instant(occurred_at if occurred_at is not None else when, f"{kind} time")
    edges.append({"source": subject["name"], "target": repo["name"], "relation": "inGitHubRepository"})
    for node in nodes[1:]:  # the thread a review or comment belongs to
        edges.append({"source": node["name"], "target": repo["name"], "relation": "inGitHubRepository"})
    props = {"sourceKind": "observed", "githubEventKind": kind, "githubActivityKey": key,
             "occurredAt": occurred}
    sender = _login(payload.get("sender"))
    if sender:
        props["githubActorLogin"] = sender
    props.update(state)
    named = mentions(*texts)
    if named:
        props["githubMentions"] = named
    activity = {"name": f"github.activity.{key[:32]}", "type": "GitHubActivity",
                "description": f"{kind} on {subject['description']}", "properties": props}
    edges += [{"source": activity["name"], "target": subject["name"], "relation": "aboutGitHubEntity"},
              {"source": activity["name"], "target": repo["name"], "relation": "inGitHubRepository"}]
    body = {
        "name": f"github:{key[:32]}",
        "graph": graph_iri(),
        "group_id": GROUP,
        "episode_body": f"GitHub {kind} in {full}",
        "source": f"github:mapping={mapping_sha[:16]}:{kind}:{key[:32]}",
        "actor": actor,
        "nodes": [activity, subject] + nodes,
        "edges": edges,
    }
    return body, key


def graph_iri() -> str:
    """The ONE operational graph every GitHub record lands in, beside the planes
    under the same parameterized namespace root (never a hardcoded host)."""
    import planes  # deferred: the mapper is pure; only the IRI comes from here
    return planes.PLANE_NS.replace("/plane/", "/operational/") + GRAPH_NAME


# ---- authentication ---------------------------------------------------------

def verify_signature(secret: bytes, header: str | None, body: bytes) -> bool:
    """GitHub's X-Hub-Signature-256: 'sha256=' + hex HMAC-SHA256 of the body.
    An empty secret verifies nothing (fail closed)."""
    if not secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret, body, hashlib.sha256).hexdigest().encode()
    given = header[len("sha256="):].strip().lower().encode("utf-8", "replace")
    return hmac.compare_digest(expected, given)


def read_secret(path: Path) -> bytes:
    try:
        return path.read_bytes().strip()
    except OSError:
        return b""


# ---- redaction: the spool keeps only what the mapper reads -----------------

_KEEP = {"node_id", "id", "number", "html_url", "url", "state", "merged", "merged_at", "merge_commit_sha",
         "draft", "created_at", "updated_at", "closed_at", "submitted_at", "commit_id", "name", "head_sha",
         "head_branch", "status", "conclusion", "run_attempt", "run_started_at", "started_at", "completed_at",
         "tag_name", "prerelease", "published_at", "full_name", "pushed_at"}
_LOGIN = {"user", "author", "actor", "triggering_actor", "sender"}


def _slim(obj: object) -> object:
    if not isinstance(obj, dict):
        return obj
    out = {k: v for k, v in obj.items() if k in _KEEP and not isinstance(v, (dict, list))}
    for k in _LOGIN & set(obj):
        if isinstance(obj[k], dict) and isinstance(obj[k].get("login"), str):
            out[k] = {"login": obj[k]["login"]}
    for side in ("head", "base"):
        if isinstance(obj.get(side), dict):
            out[side] = {k: obj[side][k] for k in ("sha", "ref") if k in obj[side]}
    if isinstance(obj.get("check_suite"), dict) and "head_branch" in obj["check_suite"]:
        out["check_suite"] = {"head_branch": obj["check_suite"]["head_branch"]}
    if "pull_request" in obj:  # an issue that is a PR: keep only the marker
        out["pull_request"] = {}
    if isinstance(obj.get("body"), str):  # text never stored; its mentions are
        out["body"] = " ".join("@" + m for m in mentions(obj["body"]))
    return out


def redact(payload: object) -> object:
    """The payload with only the fields the mapper (and poll hydration) reads.
    Drops commits, head_commit, pusher, emails and text; a body becomes its
    @-mentions, so a replay maps byte-identically."""
    if not isinstance(payload, dict):
        return payload
    out: dict = {}
    for k in ("action", "ref", "before", "after", "forced", "_raw_action", "_hydrate"):
        if k in payload:
            out[k] = payload[k]
    for k, v in payload.items():
        if k == "sender" and isinstance(v, dict):
            out[k] = {"login": v.get("login")} if isinstance(v.get("login"), str) else {}
        elif k in ("repository", "pull_request", "review", "comment", "issue", "check_run",
                 "workflow_run", "release"):
            out[k] = _slim(v)
    return out


# ---- spool ------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS deliveries (
  id TEXT PRIMARY KEY, event TEXT NOT NULL, body BLOB NOT NULL, occurred_at TEXT,
  received_at REAL NOT NULL, state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
  next_at REAL NOT NULL DEFAULT 0, activity TEXT, reason TEXT);
CREATE INDEX IF NOT EXISTS deliveries_due ON deliveries (state, next_at);
CREATE TABLE IF NOT EXISTS activities (key TEXT PRIMARY KEY, delivery TEXT NOT NULL, written_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS etags (url TEXT PRIMARY KEY, etag TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS repos (full_name TEXT PRIMARY KEY, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS budget (hour INTEGER PRIMARY KEY, spent INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value REAL NOT NULL);
"""


class Spool:
    """The one durable queue every transport feeds. Thread-safe by a lock."""

    def __init__(self, path: str):
        old = os.umask(0o077)  # the db and its -wal/-shm are owner-only (0600)
        try:
            if path != ":memory:":
                os.close(os.open(path, os.O_CREAT | os.O_RDWR, 0o600))
            self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
            self.lock = threading.Lock()
            with self.lock:
                if path != ":memory:":
                    self.conn.execute("PRAGMA journal_mode=WAL")
                self.conn.execute("PRAGMA synchronous=FULL")
                self.conn.executescript(SCHEMA)
        finally:
            os.umask(old)
        if path != ":memory:":
            for suffix in ("", "-wal", "-shm"):
                if os.path.exists(path + suffix):
                    os.chmod(path + suffix, 0o600)

    def add(self, delivery: str, event: str, payload: object, *, occurred_at: str | None = None,
            now: float | None = None, state: str = "pending") -> str:
        """Spool one delivery, REDACTED first: raw bodies never touch disk."""
        now = time.time() if now is None else now
        body = json.dumps(redact(payload), sort_keys=True).encode()
        with self.lock:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO deliveries (id, event, body, occurred_at, received_at, state) "
                "VALUES (?,?,?,?,?,?)", (delivery, event, body, occurred_at, now, state))
        return "accepted" if cur.rowcount == 1 else "duplicate"

    def pending(self) -> int:
        with self.lock:
            return self.conn.execute(
                "SELECT COUNT(*) FROM deliveries WHERE state IN ('pending','hydrate')").fetchone()[0]

    def due_hydration(self, now: float, limit: int = 50) -> list[tuple]:
        with self.lock:
            return self.conn.execute(
                "SELECT id, event, body, attempts FROM deliveries WHERE state='hydrate' AND next_at<=? "
                "ORDER BY received_at, id LIMIT ?", (now, limit)).fetchall()

    def hydrated(self, delivery: str, payload: object) -> None:
        body = json.dumps(redact(payload), sort_keys=True).encode()
        with self.lock:
            self.conn.execute("UPDATE deliveries SET body=?, state='pending', next_at=0, reason=NULL WHERE id=?",
                              (body, delivery))

    def get_state(self, key: str, default: float = 0.0) -> float:
        with self.lock:
            row = self.conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return float(row[0]) if row else default

    def set_state(self, key: str, value: float) -> None:
        with self.lock:
            self.conn.execute("INSERT OR REPLACE INTO state (key, value) VALUES (?,?)", (key, value))

    def has(self, delivery: str) -> bool:
        with self.lock:
            return self.conn.execute("SELECT 1 FROM deliveries WHERE id=?", (delivery,)).fetchone() is not None

    def due(self, now: float, limit: int = 50) -> list[tuple]:
        with self.lock:
            return self.conn.execute(
                "SELECT id, event, body, occurred_at, attempts FROM deliveries "
                "WHERE state='pending' AND next_at<=? ORDER BY received_at, id LIMIT ?", (now, limit)).fetchall()

    def settle(self, delivery: str, state: str, *, reason: str | None = None, activity: str | None = None) -> None:
        with self.lock:
            self.conn.execute("UPDATE deliveries SET state=?, reason=?, activity=? WHERE id=?",
                              (state, reason, activity, delivery))

    def written(self, delivery: str, activity: str, now: float) -> None:
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                self.conn.execute("INSERT OR IGNORE INTO activities (key, delivery, written_at) VALUES (?,?,?)",
                                  (activity, delivery, now))
                self.conn.execute("UPDATE deliveries SET state='written', reason=NULL, activity=? WHERE id=?",
                                  (activity, delivery))
                self.conn.execute("COMMIT")
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise

    def activity_written(self, key: str) -> bool:
        with self.lock:
            return self.conn.execute("SELECT 1 FROM activities WHERE key=?", (key,)).fetchone() is not None

    def retry(self, delivery: str, attempts: int, now: float, reason: str) -> None:
        delay = min(5 * 2 ** attempts, 900)
        with self.lock:
            self.conn.execute("UPDATE deliveries SET attempts=?, next_at=?, reason=? WHERE id=?",
                              (attempts + 1, now + delay, reason[:500], delivery))

    def counts(self) -> dict:
        with self.lock:
            return dict(self.conn.execute("SELECT state, COUNT(*) FROM deliveries GROUP BY state").fetchall())

    def purge(self, retain_days: float, now: float) -> int:
        with self.lock:
            cur = self.conn.execute("DELETE FROM deliveries WHERE state NOT IN ('pending','hydrate') AND received_at<?",
                                    (now - retain_days * 86400,))
        return cur.rowcount

    # poller state
    def etag(self, url: str) -> str | None:
        with self.lock:
            row = self.conn.execute("SELECT etag FROM etags WHERE url=?", (url,)).fetchone()
        return row[0] if row else None

    def set_etag(self, url: str, etag: str) -> None:
        with self.lock:
            self.conn.execute("INSERT OR REPLACE INTO etags (url, etag) VALUES (?,?)", (url, etag))

    def repo(self, full_name: str) -> dict | None:
        with self.lock:
            row = self.conn.execute("SELECT body FROM repos WHERE full_name=?", (full_name,)).fetchone()
        return json.loads(row[0]) if row else None

    def set_repo(self, full_name: str, body: dict) -> None:
        with self.lock:
            self.conn.execute("INSERT OR REPLACE INTO repos (full_name, body) VALUES (?,?)",
                              (full_name, json.dumps(body, sort_keys=True)))

    def spend(self, now: float, limit: int) -> bool:
        """Take one request from this hour's budget; False when it is spent."""
        hour = int(now // 3600)
        with self.lock:
            row = self.conn.execute("SELECT spent FROM budget WHERE hour=?", (hour,)).fetchone()
            spent = row[0] if row else 0
            if spent >= limit:
                return False
            self.conn.execute("INSERT OR REPLACE INTO budget (hour, spent) VALUES (?,?)", (hour, spent + 1))
            self.conn.execute("DELETE FROM budget WHERE hour<?", (hour - 48,))
        return True


# ---- drain ------------------------------------------------------------------

class Writer:
    """Posts episodes; registers and labels the github graph once."""

    def __init__(self):
        self.ensured: set[str] = set()

    def __call__(self, body: dict) -> None:
        import planes
        import windows
        graph = body["graph"]
        if graph not in self.ensured:
            # Register AND label, both or neither (the windows/planes discipline);
            # one operational graph, never frozen by month.
            planes._post("/graph/create", {"graph": graph}, client=CLIENT)
            planes._post("/graph/label", {"graph": graph, "actor": CLIENT,
                                          "timestamp": body["nodes"][0]["properties"]["occurredAt"],
                                          **windows.WINDOW_LABEL}, client=CLIENT)
            self.ensured.add(graph)
        planes._post("/episode", body, client=CLIENT)


def print_writer(body: dict) -> None:
    print(json.dumps(body, sort_keys=True), flush=True)


def drain_once(spool: Spool, *, actor: str, writer, repos: set[str] | None = None,
               now: float | None = None, mapping: tuple[dict, str] | None = None) -> dict:
    """Map and write every due delivery once. Returns counts by outcome."""
    now = time.time() if now is None else now
    mapping, sha = mapping or load_mapping()
    out: dict[str, int] = {}

    def tally(outcome: str) -> None:
        out[outcome] = out.get(outcome, 0) + 1

    for delivery, event, raw, occurred_at, attempts in spool.due(now):
        try:
            payload = json.loads(raw)
            full = ((payload.get("repository") or {}).get("full_name") if isinstance(payload, dict) else None)
            if repos is not None and event != "ping" and full not in repos:
                spool.settle(delivery, "foreign", reason=f"repository {full!r} is not watched")
                tally("foreign")
                continue
            body, key = episode_for(event, payload, actor=actor, occurred_at=occurred_at,
                                    mapping=mapping, mapping_sha=sha)
        except Ignored as exc:
            spool.settle(delivery, "ignored", reason=str(exc))
            tally("ignored")
            continue
        except (Quarantined, ValueError) as exc:
            spool.settle(delivery, "quarantined", reason=str(exc)[:500])
            tally("quarantined")
            continue
        if spool.activity_written(key):
            spool.settle(delivery, "duplicate", activity=key, reason="activity already written")
            tally("duplicate")
            continue
        try:
            writer(body)
        except Exception as exc:  # noqa: BLE001 - a failed write stays pending, never lost
            spool.retry(delivery, attempts, now, f"{type(exc).__name__}: {exc}")
            tally("retry")
            continue
        spool.written(delivery, key, now)
        tally("written")
    return out


# ---- webhook receiver -------------------------------------------------------

def make_handler(spool: Spool, secret: bytes, repos: set[str], path: str, wake: threading.Event):
    class Handler(BaseHTTPRequestHandler):
        server_version = "camayoc-github-ingress"
        timeout = 10  # a slow client cannot hold a thread

        def _send(self, code: int, payload: dict) -> None:
            data = json.dumps(payload, sort_keys=True).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, fmt, *args):  # never the body, never the secret
            sys.stderr.write("github-ingress %s %s\n" % (self.address_string(), fmt % args))

        def do_GET(self):
            if self.path == "/healthz":
                self._send(200, {"ok": True, "spool": spool.counts()})
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):
            if self.path != path:
                return self._send(404, {"error": "not found"})
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                return self._send(411, {"error": "length required"})
            if length < 0 or length > MAX_BODY:
                return self._send(413, {"error": "body too large"})
            body = self.rfile.read(length)
            if not verify_signature(secret, self.headers.get("X-Hub-Signature-256"), body):
                return self._send(401, {"error": "signature"})
            event = self.headers.get("X-GitHub-Event", "")
            delivery = self.headers.get("X-GitHub-Delivery", "")
            if not re.fullmatch(r"[a-z_]{1,64}", event) or not re.fullmatch(r"[0-9A-Fa-f-]{8,64}", delivery):
                return self._send(400, {"error": "missing or malformed event/delivery header"})
            try:
                payload = json.loads(body)
            except ValueError:
                return self._send(400, {"error": "body is not JSON"})
            full = (payload.get("repository") or {}).get("full_name") if isinstance(payload, dict) else None
            if event == "ping":
                return self._send(200, {"outcome": "pong"})
            if full not in repos:
                return self._send(202, {"outcome": "ignored", "reason": "repository not watched"})
            if spool.pending() >= MAX_PENDING:
                return self._send(503, {"error": "spool full; GitHub will show the delivery as failed"})
            outcome = spool.add(delivery, event, payload)
            wake.set()
            self._send(202 if outcome == "accepted" else 200, {"outcome": outcome, "delivery": delivery})

    return Handler


def run_drain_loop(spool: Spool, *, actor: str, writer, repos: set[str], wake: threading.Event,
                   stop: threading.Event, period: float = 15.0, retain_days: float = RETAIN_DAYS) -> None:
    while not stop.is_set():
        try:
            counts = drain_once(spool, actor=actor, writer=writer, repos=repos)
            if counts:
                sys.stderr.write(f"github-ingress drain {json.dumps(counts, sort_keys=True)}\n")
            spool.purge(retain_days, time.time())
        except Exception as exc:  # noqa: BLE001 - the loop must survive one bad tick
            sys.stderr.write(f"github-ingress drain error {type(exc).__name__}: {exc}\n")
        wake.wait(period)
        wake.clear()


# ---- poller -----------------------------------------------------------------

class GhTransport:
    """GET through `gh api -i`: gh holds the credential, nothing here reads it."""

    def get(self, path: str, etag: str | None = None) -> tuple[int, dict, bytes]:
        cmd = ["gh", "api", "-i", path]
        if etag:
            cmd[3:3] = ["-H", f"If-None-Match: {etag}"]
        proc = subprocess.run(cmd, capture_output=True, timeout=60, check=False)
        return parse_http(proc.stdout)


def parse_http(raw: bytes) -> tuple[int, dict, bytes]:
    head, sep, body = raw.partition(b"\r\n\r\n")
    if not sep:
        head, sep, body = raw.partition(b"\n\n")
    lines = head.decode("latin-1").splitlines()
    if not lines or not lines[0].startswith("HTTP/"):
        return 0, {}, b""
    status = int(lines[0].split()[1])
    headers = {}
    for line in lines[1:]:
        name, _, value = line.partition(":")
        headers[name.strip().lower()] = value.strip()
    return status, headers, body


class RateLimited(Exception):
    """GitHub said stop (403/429): the cycle ends and a backoff is persisted."""


class Unavailable(Exception):
    """This cycle cannot make the request (budget spent, transient error)."""


class Poller:
    """Budgeted, ETag'd reads of the Events API and Actions runs into the spool.

    A 403 or 429 ends the cycle at once and persists `backoff_until` (from
    Retry-After, else x-ratelimit-reset when remaining is 0, else exponential),
    and no call at all is made until it passes. A trimmed Events API object is
    spooled as `hydrate`; its fetch is retried with attempts and backoff, and
    only a real 404/410 lets it through unhydrated (to be quarantined)."""

    def __init__(self, spool: Spool, transport, repos: list[str], *, budget_per_hour: int = 300,
                 reserve: int = 1000, mapping: tuple[dict, str] | None = None):
        self.spool, self.transport, self.repos = spool, transport, repos
        self.budget, self.reserve = budget_per_hour, reserve
        self.mapping, self.sha = mapping or load_mapping()
        self.poll_interval = 60

    def _back_off(self, now: float, headers: dict, receipt: dict) -> None:
        retry_after = headers.get("retry-after", "")
        reset = headers.get("x-ratelimit-reset", "")
        n = int(self.spool.get_state("backoff_n"))
        if retry_after.isdigit():
            until = now + int(retry_after)
        elif headers.get("x-ratelimit-remaining") == "0" and reset.isdigit():
            until = float(reset)
        else:
            until = now + min(60 * 2 ** n, 3600)
        until = max(until, now + 1)
        self.spool.set_state("backoff_until", until)
        self.spool.set_state("backoff_n", n + 1)
        receipt["rate_limited"] = until

    def _get(self, path: str, now: float, receipt: dict, *, etag_key: str | None = None,
             counted: bool = True) -> tuple[int, dict, bytes]:
        if counted and not self.spool.spend(now, self.budget):
            receipt["over_budget"] = True
            raise Unavailable("hourly budget spent")
        status, headers, body = self.transport.get(path, self.spool.etag(etag_key) if etag_key else None)
        if counted:
            receipt["requests"] = receipt.get("requests", 0) + 1
        if headers.get("x-poll-interval", "").isdigit():
            self.poll_interval = max(self.poll_interval, int(headers["x-poll-interval"]))
        if status in (403, 429):
            self._back_off(now, headers, receipt)
            raise RateLimited(f"{path}: HTTP {status}")
        return status, headers, body

    def _json(self, path: str, now: float, receipt: dict, *, etag_key: str | None = None):
        """Parsed body, "304", or None (an error, recorded in the receipt)."""
        status, headers, body = self._get(path, now, receipt, etag_key=etag_key)
        if status == 304:
            receipt["not_modified"] = receipt.get("not_modified", 0) + 1
            return "304"
        if status != 200:
            receipt.setdefault("errors", []).append(f"{path}: HTTP {status}")
            return None
        try:
            parsed = json.loads(body)
        except ValueError:
            receipt.setdefault("errors", []).append(f"{path}: body is not JSON")
            return None
        if etag_key and headers.get("etag"):
            self.spool.set_etag(etag_key, headers["etag"])
        return parsed

    def _repo(self, full: str, now: float, receipt: dict) -> dict | None:
        cached = self.spool.repo(full)
        if cached:
            return cached
        got = self._json(f"/repos/{full}", now, receipt)
        if isinstance(got, dict) and got.get("node_id"):
            keep = {k: got.get(k) for k in ("id", "node_id", "name", "full_name", "html_url", "default_branch")}
            self.spool.set_repo(full, keep)
            return keep
        return None

    def normalize(self, ev: dict, repo: dict) -> tuple[str, dict] | None:
        """One Events API event -> (webhook event name, webhook-shaped payload).
        Objects the Events API trims are named in `_hydrate` for a later fetch."""
        api = self.mapping["events_api"]
        event = api["types"].get(ev.get("type"))
        if event is None:
            return None
        raw = ev.get("payload") or {}
        sender = {"login": (ev.get("actor") or {}).get("login")}
        if event == "push":
            return event, {"ref": raw.get("ref"), "before": raw.get("before"), "after": raw.get("head"),
                           "repository": repo, "sender": sender}
        payload = dict(raw)
        if "action" in payload:
            payload["_raw_action"] = payload["action"]
            payload["action"] = api["actions"].get(ev["type"], {}).get(payload["action"], payload["action"])
        payload["repository"] = repo
        payload["sender"] = sender
        pr = payload.get("pull_request")
        if isinstance(pr, dict) and not pr.get("node_id") and isinstance(pr.get("url"), str):
            payload["_hydrate"] = {"pull_request": pr["url"]}
        return event, payload

    @staticmethod
    def _apply(event: str, payload: dict, key: str, full: dict) -> None:
        trimmed = payload.get(key) or {}
        if event != "pull_request":
            payload[key] = full
            return
        # The fetched object is the PR NOW; the event's own head/base and action
        # decide the state it left at the event.
        full = dict(full)
        for side in ("head", "base"):
            if isinstance(trimmed.get(side), dict):
                full[side] = {**(full.get(side) or {}), **trimmed[side]}
        full.pop("draft", None)
        action = payload.get("action")
        full["state"] = "closed" if action == "closed" else ("open" if action in ("opened", "reopened") else full.get("state"))
        full["merged"] = payload.get("_raw_action") == "merged"
        payload[key] = full

    def hydrate_due(self, now: float, receipt: dict) -> None:
        cache: dict[str, object] = {}
        for delivery, event, raw, attempts in self.spool.due_hydration(now):
            payload = json.loads(raw)
            try:
                for key, url in (payload.get("_hydrate") or {}).items():
                    if url not in cache:
                        if not url.startswith(API + "/"):
                            cache[url] = "gone"
                        else:
                            status, _, body = self._get(url[len(API):], now, receipt)
                            if status == 200:
                                cache[url] = json.loads(body)
                                receipt["hydrated"] = receipt.get("hydrated", 0) + 1
                            elif status in (404, 410):
                                cache[url] = "gone"
                            else:
                                cache[url] = Unavailable(f"{url}: HTTP {status}")
                    if isinstance(cache[url], Unavailable):
                        raise cache[url]  # one failed fetch defers every event naming it
                    if cache[url] != "gone":
                        self._apply(event, payload, key, cache[url])
            except (Unavailable, ValueError) as exc:
                self.spool.retry(delivery, attempts, now, f"hydration: {exc}")
                receipt["hydration_retry"] = receipt.get("hydration_retry", 0) + 1
                if isinstance(exc, Unavailable) and receipt.get("over_budget"):
                    break
                continue
            except RateLimited:
                self.spool.retry(delivery, attempts, now, "hydration: rate limited")
                raise
            payload.pop("_hydrate", None)
            payload.pop("_raw_action", None)
            self.spool.hydrated(delivery, payload)  # gone objects stay trimmed: drain quarantines

    def cycle(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        receipt: dict = {"at": now}
        until = self.spool.get_state("backoff_until")
        if now < until:
            receipt["skipped"] = f"backoff until {until:.0f}"
            return receipt
        try:
            status, _, body = self._get("/rate_limit", now, receipt, counted=False)  # free
            try:
                remaining = json.loads(body)["resources"]["core"]["remaining"] if status == 200 else None
            except (ValueError, KeyError, TypeError):
                remaining = None
            receipt["remaining"] = remaining
            if remaining is None or remaining < self.reserve:
                receipt["skipped"] = "rate limit unknown" if remaining is None else "below reserve"
                return receipt
            for full in self.repos:
                try:
                    self._poll_repo(full, now, receipt)
                except Unavailable:
                    break
            try:
                self.hydrate_due(now, receipt)
            except Unavailable:
                pass
        except RateLimited:
            return receipt
        self.spool.set_state("backoff_n", 0)
        return receipt

    def _spool(self, delivery: str, event: str, payload: dict, now: float, receipt: dict,
               occurred_at: str | None = None) -> None:
        state = "hydrate" if payload.get("_hydrate") else "pending"
        outcome = self.spool.add(delivery, event, payload, occurred_at=occurred_at, now=now, state=state)
        receipt[outcome] = receipt.get(outcome, 0) + 1

    def _poll_repo(self, full: str, now: float, receipt: dict) -> None:
        repo = self._repo(full, now, receipt)
        if repo is None:
            receipt.setdefault("errors", []).append(f"{full}: repository unknown")
            return
        page = self._json(f"/repos/{full}/events?per_page=100", now, receipt, etag_key=f"events:{full}")
        if isinstance(page, list):
            for ev in sorted(page, key=lambda e: (e.get("created_at") or "", e.get("id") or "")):
                delivery = f"poll:{ev.get('id')}"
                if not ev.get("id") or self.spool.has(delivery):
                    continue
                shaped = self.normalize(ev, repo)
                if shaped is not None:
                    self._spool(delivery, shaped[0], shaped[1], now, receipt, occurred_at=ev.get("created_at"))
        runs = self._json(f"/repos/{full}/actions/runs?per_page=20", now, receipt, etag_key=f"runs:{full}")
        if isinstance(runs, dict):
            for run in runs.get("workflow_runs") or []:
                action = {"completed": "completed", "in_progress": "in_progress"}.get(run.get("status"), "requested")
                delivery = f"poll:run:{run.get('id')}:{run.get('run_attempt')}:{run.get('status')}:{run.get('conclusion')}"
                if self.spool.has(delivery):
                    continue
                payload = {"action": action, "workflow_run": run, "repository": repo,
                           "sender": {"login": (run.get("triggering_actor") or run.get("actor") or {}).get("login")}}
                self._spool(delivery, "workflow_run", payload, now, receipt)


# ---- CLI --------------------------------------------------------------------

def _replay(directory: Path, *, actor: str, writer, spool: Spool) -> dict:
    """Recorded deliveries ({headers, body}) through the spool and drain."""
    for path in sorted(directory.glob("*.json")):
        rec = json.loads(path.read_text())
        spool.add(rec["headers"]["X-GitHub-Delivery"], rec["headers"]["X-GitHub-Event"], rec["body"])
    return drain_once(spool, actor=actor, writer=writer)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("map", help="one payload -> /episode body")
    m.add_argument("event")
    m.add_argument("input", type=Path)
    m.add_argument("--actor", required=True)
    r = sub.add_parser("replay", help="recorded deliveries through spool and drain")
    r.add_argument("directory", type=Path)
    r.add_argument("--actor", required=True)
    r.add_argument("--spool", default=":memory:")
    r.add_argument("--post", action="store_true")
    s = sub.add_parser("serve", help="webhook receiver + drain")
    s.add_argument("--listen", default="127.0.0.1:3040")
    s.add_argument("--path", default="/hooks/github")
    s.add_argument("--secret-file", type=Path, required=True)
    p = sub.add_parser("poll", help="budgeted poller + drain")
    p.add_argument("--budget-per-hour", type=int, default=300)
    p.add_argument("--reserve", type=int, default=1000)
    p.add_argument("--interval", type=int, default=60)
    p.add_argument("--once", action="store_true")
    for cmd in (s, p):
        cmd.add_argument("--spool", required=True)
        cmd.add_argument("--repo", action="append", required=True, help="owner/name; repeat")
        cmd.add_argument("--actor", required=True)
        cmd.add_argument("--dry-run", action="store_true", help="print episodes instead of posting")
        cmd.add_argument("--retain-days", type=float, default=RETAIN_DAYS)
    d = sub.add_parser("drain", help="one drain pass over the spool")
    d.add_argument("--spool", required=True)
    d.add_argument("--actor", required=True)
    d.add_argument("--post", action="store_true")
    st = sub.add_parser("status", help="spool counts by state")
    st.add_argument("--spool", required=True)
    args = ap.parse_args(argv)

    if args.cmd == "map":
        try:
            body, _ = episode_for(args.event, json.loads(args.input.read_text()), actor=args.actor)
        except Ignored as exc:
            print(json.dumps({"outcome": "ignored", "reason": str(exc)}))
            return 0
        except (Quarantined, ValueError) as exc:
            print(json.dumps({"outcome": "quarantined", "input": str(args.input), "error": str(exc)}))
            return 2
        print(json.dumps(body, sort_keys=True))
        return 0
    if args.cmd == "replay":
        counts = _replay(args.directory, actor=args.actor, writer=Writer() if args.post else print_writer,
                         spool=Spool(args.spool))
        print(json.dumps({"replay": counts}, sort_keys=True))
        return 0
    if args.cmd == "status":
        print(json.dumps(Spool(args.spool).counts(), sort_keys=True))
        return 0
    if args.cmd == "drain":
        print(json.dumps(drain_once(Spool(args.spool), actor=args.actor,
                                    writer=Writer() if args.post else print_writer), sort_keys=True))
        return 0

    for full in args.repo:
        if not _REPO.match(full):
            ap.error(f"--repo {full!r} is not owner/name")
    spool = Spool(args.spool)
    repos = set(args.repo)
    writer = print_writer if args.dry_run else Writer()
    wake, stop = threading.Event(), threading.Event()
    if args.cmd == "serve":
        secret = read_secret(args.secret_file)
        if not secret:
            sys.stderr.write("github-ingress: secret file empty or unreadable; every delivery will be refused\n")
        host, _, port = args.listen.rpartition(":")
        server = ThreadingHTTPServer((host or "127.0.0.1", int(port)),
                                     make_handler(spool, secret, repos, args.path, wake))
        worker = threading.Thread(target=run_drain_loop, daemon=True, kwargs=dict(
            spool=spool, actor=args.actor, writer=writer, repos=repos, wake=wake, stop=stop,
            retain_days=args.retain_days))
        worker.start()
        try:
            server.serve_forever()
        finally:
            stop.set()
            wake.set()
        return 0
    poller = Poller(spool, GhTransport(), args.repo, budget_per_hour=args.budget_per_hour, reserve=args.reserve)
    while True:
        receipt = poller.cycle()
        receipt["drain"] = drain_once(spool, actor=args.actor, writer=writer, repos=repos)
        spool.purge(args.retain_days, time.time())
        print(json.dumps(receipt, sort_keys=True), flush=True)
        if args.once:
            return 0
        time.sleep(max(args.interval, poller.poll_interval))


if __name__ == "__main__":
    raise SystemExit(main())
