#!/usr/bin/env python3
"""GitHub activity -> governed Quipu episodes: one ingress (aegis-c01c.3).

Design: docs/design/github-ingress.md. Shapes: shapes/github.shapes.ttl.
Admission data: mappings/github.json (its sha256 rides in every source).

    github_ingress.py map EVENT FILE --actor A          one payload -> /episode body
    github_ingress.py replay DIR --actor A [--post]     recorded deliveries, end to end
    github_ingress.py serve --secret-file F --spool DB --repo O/N ... --actor A [--no-drain]
    github_ingress.py poll  --spool DB --repo O/N ... --actor A [--once]
    github_ingress.py drain --spool DB --actor A [--post] [--loop SECONDS]
    github_ingress.py status --spool DB

Every transport puts deliveries into ONE SQLite spool and one drain maps them,
so the webhook receiver and the budgeted poller cannot disagree about meaning:

- receiver: HMAC-SHA256 (X-Hub-Signature-256) over the exact body, checked in
  constant time BEFORE any JSON parse; an empty or missing secret refuses
  everything. Spool first, then 202; a known X-GitHub-Delivery answers 409.
  Internet-facing hardening (aegis-xxoa.5): only POST on the one path (405 for
  other methods there, a bare 404 elsewhere), error answers carry no body, a
  body cap, a header-count cap, a bounded number of concurrent requests, an
  event allowlist (the mapped events + ping), and a token bucket on REJECTED
  requests that answers 429 before reading the body once it is spent. Every
  request logs one line: verdict, status, event and delivery id (each only if
  well-formed), never a body or a header value. `--no-drain` holds no Quipu
  credential: a separate `drain --post --loop` process writes.
- poller: only through `gh` (it holds the credential; the aegis-izs shim meters
  it), ETag conditional reads, an hourly budget and a rate-limit reserve. A
  403/429 ends the cycle and persists a backoff; a failed object fetch stays
  retryable in the spool (state `hydrate`), never quarantined unless 404/410.
- spool: REDACTED before it touches disk (no commits, pusher, emails or text;
  a body becomes its @-mentions), mode 0600 (0660 with --spool-group-shared,
  for a receiver and a drain that run as different users), kept
  --retain-days (default 2).
- drain: payload -> episode in the ONE `github` operational graph. The activity key (payload fields only) is
  recorded in the spool, so the same change from both transports is written
  once. A re-sent body is byte-identical, so a lost Quipu response is safe to
  retry. An event the mapping cannot place is QUARANTINED with its reason.
  The repository node is asserted only when its content differs from the
  digest last written (spool table repo_assertions); otherwise the episode
  names it by IRI in its edges alone (aegis-xxoa.5.1). A retry may therefore
  drop the repository node that a lost first attempt carried, and the
  activity facts stay the same.

Only identifiers, times, verdict words and @-mentions leave the payload:
never comment, review or PR bodies, commit messages or logs.
"""

from __future__ import annotations

import argparse
import collections
import errno
import hashlib
import hmac
import ipaddress
import json
import os
import re
import socket
import sqlite3
import stat
import subprocess
import sys
import threading
import time
import urllib.error
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
MAX_HEADERS = 64  # GitHub sends about 20; the stdlib's own ceiling is 100
MAX_CONCURRENT = 32  # requests in flight; more are closed without an answer
REQUEST_DEADLINE = 10.0  # seconds for one whole request: headers, body and answer
REJECT_BURST = 30  # rejected requests allowed back to back ...
REJECT_RATE = 1.0  # ... then this many per second; beyond that, 429 before reading
_EVENT = re.compile(r"[a-z_]{1,64}")
_DELIVERY = re.compile(r"[0-9A-Fa-f-]{8,64}")
API = "https://api.github.com"
ZERO_SHA = "0" * 40
OPEN_PAGE = 100  # one page of open PRs per repo per cycle; a full page is treated as truncated

_SHA = re.compile(r"^[0-9a-f]{40}$")
_NODE = re.compile(r"^[A-Za-z0-9_-]+$")
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_FENCE = re.compile(r"```.*?```|`[^`\n]*`", re.S)
_MENTION = re.compile(r"(?<![A-Za-z0-9_`/.@-])@([A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38})(?![A-Za-z0-9_/-])")
_REVIEW_STATES = {"approved", "changes_requested", "commented", "dismissed"}
_STATUSES = {"queued", "in_progress", "completed", "requested", "waiting", "pending"}
#: Deliveries acknowledged without a write (aegis-n0wc): a second non-terminal
#: transition of one run. Readers take the latest fact per run and treat any
#: non-completed status as pending (Chaski ci_state), so `workflow_run.requested`
#: already records "pending" and `completed` records the outcome. The repeat
#: cost one Quipu /episode per run during CI bursts. Kept: check_run.created,
#: workflow_run.requested and every completed. Residual risk (ian, n0wc): if a
#: requested delivery is lost, the run is unseen until it completes.
_REPEAT_TRANSITIONS = {("workflow_run", "in_progress")}
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
    if author and cls in ("GitHubPullRequest", "GitHubIssue", "GitHubDiscussion", "GitHubComment", "GitHubReview"):
        props["githubAuthorLogin"] = author
    if cls in ("GitHubPullRequest", "GitHubIssue", "GitHubDiscussion") and _opt(obj, "created_at"):
        props["githubCreatedAt"] = _instant(obj["created_at"], f"{where}.created_at")
    props.update(extra or {})
    return {"name": node_name(node_id), "type": cls,
            "description": label or f"{repo['properties']['githubFullName']} {where} {node_id}",
            "properties": props}


def _thread(payload: dict, key: str, repo: dict) -> dict:
    obj = payload.get(key)
    is_pr = key == "pull_request" or (isinstance(obj, dict) and "pull_request" in obj)
    cls = "GitHubDiscussion" if key == "discussion" else "GitHubPullRequest" if is_pr else "GitHubIssue"
    full = repo["properties"]["githubFullName"]
    number = _need(obj, "number", key, int)
    return _entity(cls, obj, key, repo, number=True, label=f"{full}#{number}")


def episode_for(event: str, payload: object, *, actor: str, occurred_at: str | None = None,
                mapping: dict | None = None, mapping_sha: str | None = None) -> tuple[dict, str]:
    """(the governed /episode body, the activity key digest) for one delivery.

    Raises Ignored (ping, or a repeat non-terminal transition in
    _REPEAT_TRANSITIONS), Quarantined (unmapped, malformed) or MissingField
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
    if (event, action) in _REPEAT_TRANSITIONS:
        raise Ignored(f"{event}.{action} repeats a non-terminal state already recorded")

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
            if isinstance(obj.get("mergeable"), bool):
                state["githubMergeable"] = obj["mergeable"]
            if obj.get("mergeable_state") in {"clean", "dirty", "blocked", "behind", "unstable", "has_hooks", "draft", "unknown"}:
                state["githubMergeableState"] = obj["mergeable_state"]
            if _sha(obj["base"].get("sha")):
                state["githubBaseSha"] = obj["base"]["sha"]
            if merged:
                mc = _sha(obj.get("merge_commit_sha"))
                if not mc:
                    raise MissingField("merged pull request without merge_commit_sha", obj.get("url"))
                state["githubMergeCommitSha"] = mc
            if action in ("opened", "edited"):
                texts.append(obj.get("body"))
            when = {"opened": obj.get("created_at"), "closed": obj.get("closed_at")}.get(action, obj.get("updated_at"))
            if action == "observed":
                # Legacy list sightings retain their semantic identity. Detail
                # transitions also bind their once-retained observation time:
                # a return to a prior state is a new append-only observation.
                key = _digest(kind, node_id, head, st, merged, obj.get("updated_at"),
                              state.get("githubBaseSha"), state.get("githubMergeable"),
                              state.get("githubMergeableState"), state.get("githubDraft"))
                if occurred_at is not None:
                    key = _digest(key, _instant(occurred_at, "PR observation time"))
            else:
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
        elif event == "discussion":
            subject = _thread(payload, "discussion", repo)
            texts.append(obj.get("body"))
            when = obj.get("created_at") if action == "created" else obj.get("updated_at")
            key = _digest(kind, node_id, "" if action == "created" else obj.get("updated_at"))
        elif event in ("pull_request_review", "pull_request_review_comment", "issue_comment", "discussion_comment"):
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
        elif event in ("check_run", "check_suite", "workflow_run"):
            head = _sha(_need(obj, "head_sha", rule["object"]))
            if not head:
                raise Quarantined(f"{rule['object']}.head_sha is not 40-hex")
            extra = {"commitSha": head}
            if event != "check_suite":
                extra["githubName"] = _need(obj, "name", rule["object"])
            subject = _entity(cls, obj, rule["object"], repo, extra=extra,
                              label=f"{full} {event} {node_id}")
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
                branch = _opt(obj if event == "check_suite" else obj.get("check_suite") or {}, "head_branch")
                when = (obj.get("updated_at") if event == "check_suite" else
                        obj.get("completed_at") if action == "completed" else obj.get("started_at"))
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
         "tag_name", "prerelease", "published_at", "full_name", "pushed_at", "mergeable", "mergeable_state"}
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
                 "check_suite", "workflow_run", "release", "discussion"):
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
-- Only the verified webhook receiver inserts here, never poll/replay hydration.
CREATE TABLE IF NOT EXISTS recruitment_events (
  delivery TEXT PRIMARY KEY, event TEXT NOT NULL, body BLOB NOT NULL,
  received_at REAL NOT NULL, state TEXT NOT NULL DEFAULT 'pending', reason TEXT);
CREATE TABLE IF NOT EXISTS etags (url TEXT PRIMARY KEY, etag TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS repos (full_name TEXT PRIMARY KEY, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS budget (hour INTEGER PRIMARY KEY, spent INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value REAL NOT NULL);
CREATE TABLE IF NOT EXISTS open_prs (repo TEXT NOT NULL, node_id TEXT NOT NULL, url TEXT NOT NULL,
  number INTEGER, updated_at TEXT, PRIMARY KEY (repo, node_id));
CREATE TABLE IF NOT EXISTS pr_observations (repo TEXT NOT NULL, node_id TEXT NOT NULL,
  state_key TEXT NOT NULL, sequence INTEGER NOT NULL, PRIMARY KEY (repo, node_id));
-- aegis-xxoa.5.1: digest of the repository node last WRITTEN to the store, so
-- its metadata is asserted once per change instead of once per delivery.
CREATE TABLE IF NOT EXISTS repo_assertions (node TEXT PRIMARY KEY, digest TEXT NOT NULL);
"""


def _secure_spool_file(path: str, mode: int, group_shared: bool) -> None:
    """Tighten a spool file we own; verify one another user owns.

    Only a file's owner may chmod it. The split drain runs as a different
    user from the receiver that created the spool, so for a foreign-owned
    file it checks the mode instead and refuses anything looser than the
    shared 0660: no world bits, never executable, and group read/write only
    when the spool is group_shared. Absent files are skipped.
    """
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return
    if not stat.S_ISREG(st.st_mode):
        raise PermissionError(f"spool path is not a regular file: {path}")
    if st.st_uid == os.geteuid():
        os.chmod(path, mode)
        return
    perms = stat.S_IMODE(st.st_mode)
    if not group_shared:
        raise PermissionError(f"owner-only spool file is owned by another user: {path}")
    if perms & ~0o660 or perms & 0o060 != 0o060:
        raise PermissionError(f"shared spool file {path} has mode {perms:04o}; expected 0660")


class Spool:
    """The one durable queue every transport feeds. Thread-safe by a lock."""

    def __init__(self, path: str, *, group_shared: bool = False):
        # The db and its -wal/-shm are owner-only (0600). group_shared (0660)
        # lets a credential-free receiver and a separate drain user share it;
        # the directory's group decides who that is. Never world-readable.
        mode = 0o660 if group_shared else 0o600
        old = os.umask(0o007 if group_shared else 0o077)
        try:
            if path != ":memory:":
                # O_NOFOLLOW: a symlinked spool path is refused before anything
                # is created or opened through it (the check below re-verifies).
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

    def add(self, delivery: str, event: str, payload: object, *, occurred_at: str | None = None,
            now: float | None = None, state: str = "pending",
            verified_webhook: bool = False) -> str:
        """Spool one delivery, REDACTED first: raw bodies never touch disk."""
        now = time.time() if now is None else now
        body = json.dumps(redact(payload), sort_keys=True).encode()
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                # Recruitment retains delivery tombstones after ordinary graph
                # history is pruned. A verified redelivery is still a replay;
                # do not reinsert it or collide with the independent inbox.
                if verified_webhook and self.conn.execute(
                        "SELECT 1 FROM recruitment_events WHERE delivery=?", (delivery,)).fetchone():
                    self.conn.execute("COMMIT")
                    return "duplicate"
                cur = self.conn.execute(
                    "INSERT OR IGNORE INTO deliveries (id, event, body, occurred_at, received_at, state) "
                    "VALUES (?,?,?,?,?,?)", (delivery, event, body, occurred_at, now, state))
                if cur.rowcount == 1 and verified_webhook:
                    self.conn.execute(
                        "INSERT INTO recruitment_events (delivery,event,body,received_at) VALUES (?,?,?,?)",
                        (delivery, event, body, now))
                self.conn.execute("COMMIT")
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
        return "accepted" if cur.rowcount == 1 else "duplicate"

    def pending(self) -> int:
        with self.lock:
            return self.conn.execute(
                "SELECT COUNT(*) FROM deliveries WHERE state IN ('pending','hydrate')").fetchone()[0]

    def recruitment_pending(self) -> int:
        with self.lock:
            return self.conn.execute(
                "SELECT COUNT(*) FROM recruitment_events WHERE state='pending'").fetchone()[0]

    def observe_pr(self, repo: str, node: str, state_key: str, payload: dict, now: float) -> str:
        """Atomically queue a transition and remember only its consecutive state.

        Cursor and queue commit together; a failed write cannot swallow a later
        retry. Retained pending bytes and observation time never change.
        """
        body = json.dumps(redact(payload), sort_keys=True).encode()
        at = datetime.fromtimestamp(now, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                last = self.conn.execute(
                    "SELECT state_key, sequence FROM pr_observations WHERE repo=? AND node_id=?",
                    (repo, node)).fetchone()
                if last and last[0] == state_key:
                    self.conn.execute("COMMIT")
                    return "duplicate"
                sequence = last[1] + 1 if last else 1
                delivery = f"poll:pr-detail-v2:{node}:{sequence}:{state_key}"
                self.conn.execute(
                    "INSERT INTO deliveries (id, event, body, occurred_at, received_at, state) "
                    "VALUES (?,?,?,?,?,'pending')", (delivery, 'pull_request', body, at, now))
                self.conn.execute("INSERT OR REPLACE INTO pr_observations VALUES (?,?,?,?)",
                                  (repo, node, state_key, sequence))
                self.conn.execute("COMMIT")
                return "accepted"
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise

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

    def repo_asserted(self, node: str) -> str | None:
        """Digest of the repository node this spool last saw written, if any."""
        with self.lock:
            row = self.conn.execute("SELECT digest FROM repo_assertions WHERE node=?", (node,)).fetchone()
        return row[0] if row else None

    def set_repo_asserted(self, node: str, digest: str | None) -> None:
        """Record (or with None, forget) the written repository digest."""
        with self.lock:
            if digest is None:
                self.conn.execute("DELETE FROM repo_assertions WHERE node=?", (node,))
            else:
                self.conn.execute("INSERT OR REPLACE INTO repo_assertions (node, digest) VALUES (?,?)",
                                  (node, digest))

    def open_prs(self, repo: str) -> dict[str, tuple]:
        """node_id -> (url, number, updated_at) of the PRs the last full sweep saw open."""
        with self.lock:
            rows = self.conn.execute("SELECT node_id, url, number, updated_at FROM open_prs WHERE repo=?",
                                     (repo,)).fetchall()
        return {r[0]: r[1:] for r in rows}

    def set_open_prs(self, repo: str, prs: dict[str, tuple]) -> None:
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                self.conn.execute("DELETE FROM open_prs WHERE repo=?", (repo,))
                self.conn.executemany(
                    "INSERT INTO open_prs (repo, node_id, url, number, updated_at) VALUES (?,?,?,?,?)",
                    [(repo, node, *row) for node, row in prs.items()])
                self.conn.execute("COMMIT")
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise

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
        try:
            planes._post("/episode", body, client=CLIENT)
        except planes.PlaneError as exc:
            # Only the episode POST can be a permanent refusal of these exact
            # bytes. A 400 from /graph/create or /graph/label is about the
            # graph, not the delivery, and must keep retrying (wu, PR #261).
            if permanent_refusal(exc):
                raise EpisodeRefused(str(exc)) from exc
            raise


class EpisodeRefused(Exception):
    """The /episode POST itself answered HTTP 400 for these exact bytes."""


def permanent_refusal(exc: BaseException) -> bool:
    """True only for an HTTP 400 answer: a deterministic refusal of fixed bytes.
    401/403 (credentials), 404 (routing), 408/429/5xx and transport or read
    timeouts stay retryable; an indeterminate write is never quarantined.
    The drain acts on it only through EpisodeRefused, which the Writer raises
    for the /episode POST alone."""
    cause = exc.__cause__
    return isinstance(cause, urllib.error.HTTPError) and cause.code == 400


def print_writer(body: dict) -> None:
    print(json.dumps(body, sort_keys=True), flush=True)


def repository_digest(body: dict) -> tuple[str, str] | None:
    """(name, content digest) of the episode's repository node, or None."""
    for node in body.get("nodes", []):
        if node.get("type") == "GitHubRepository":
            return node["name"], _digest(json.dumps(node, sort_keys=True))
    return None


def elide_repository(body: dict, written_digest: str | None) -> tuple[dict, bool]:
    """The body without its repository node when that node's content is the
    one already written (aegis-xxoa.5.1).

    Quipu dedups facts per SOURCE, and every delivery is its own source, so
    re-asserting the repository's unchanged metadata on every delivery added
    a full copy of it each time: 12,500 sources x 8 rows on one repository.
    The edges still name the repository by IRI; only its metadata is left
    out, and it is written again as soon as any of it changes."""
    found = repository_digest(body)
    if found is None or written_digest is None or found[1] != written_digest:
        return body, False
    return dict(body, nodes=[n for n in body["nodes"] if n.get("type") != "GitHubRepository"]), True


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
        repo = repository_digest(body)
        if repo is not None:
            body, elided = elide_repository(body, spool.repo_asserted(repo[0]))
        else:
            elided = False
        try:
            writer(body)
        except Exception as exc:  # noqa: BLE001 - a failed write stays pending, never lost
            if isinstance(exc, EpisodeRefused) and elided:
                # The store refused a body that relied on the repository already
                # being there (e.g. sh:class on inGitHubRepository after a store
                # rebuild). Forget the digest and retry: the next attempt asserts
                # the repository in full, and only that one can be judged permanent.
                spool.set_repo_asserted(repo[0], None)
                spool.retry(delivery, attempts, now, f"refused without repository metadata: {exc}")
                tally("retry")
                continue
            if isinstance(exc, EpisodeRefused):
                # The store judged these exact bytes and refused them (aegis-n0wc:
                # SHACL MaxCount on a repeated state change). A retry sends the
                # same bytes and gets the same 400, so retrying forever only
                # spends store time: 65 such rows cost ~43% of this client's
                # /episode calls. Quarantine with the reason; nothing is lost
                # that a retry could have written.
                spool.settle(delivery, "quarantined", reason=f"permanent refusal: {exc}"[:500])
                tally("refused")
                continue
            spool.retry(delivery, attempts, now, f"{type(exc).__name__}: {exc}")
            tally("retry")
            continue
        spool.written(delivery, key, now)
        if repo is not None and not elided:
            spool.set_repo_asserted(*repo)
        tally("written")
    return out


# ---- webhook receiver -------------------------------------------------------

class RejectLimiter:
    """A token bucket spent only by REJECTED requests. While it is empty every
    request gets 429 before its body is read, so a flood of forged deliveries
    costs one header parse each, not a body read and an HMAC."""

    def __init__(self, burst: int = REJECT_BURST, rate: float = REJECT_RATE, clock=time.monotonic):
        self.burst, self.rate, self.clock = float(burst), float(rate), clock
        self.tokens, self.at = float(burst), clock()
        self.lock = threading.Lock()

    def _refill(self) -> None:
        now = self.clock()
        self.tokens = min(self.burst, self.tokens + (now - self.at) * self.rate)
        self.at = now

    def exhausted(self) -> bool:
        with self.lock:
            self._refill()
            return self.tokens < 1.0

    def spend(self) -> None:
        with self.lock:
            self._refill()
            self.tokens = max(0.0, self.tokens - 1.0)


class Metrics:
    """Verdict counters, optionally written as a node_exporter textfile
    (atomic rename) so alerts can see a reject spike or no accepted delivery."""

    def __init__(self, path: Path | None = None, clock=time.time):
        self.path, self.clock = path, clock
        self.counts: dict[str, int] = {}
        # Starts at the start time, so "no accepted delivery for N hours" counts
        # from whichever is later: the last delivery or the restart.
        self.last_accepted = clock()
        self.lock = threading.Lock()

    def record(self, verdict: str) -> None:
        with self.lock:
            self.counts[verdict] = self.counts.get(verdict, 0) + 1
            if verdict == "accepted":
                self.last_accepted = self.clock()
            if self.path is None:
                return
            lines = ["# HELP camayoc_github_ingress_requests_total Webhook requests by verdict since start.",
                     "# TYPE camayoc_github_ingress_requests_total counter"]
            lines += [f'camayoc_github_ingress_requests_total{{verdict="{v}"}} {n}'
                      for v, n in sorted(self.counts.items())]
            lines += ["# HELP camayoc_github_ingress_last_accepted_timestamp_seconds Last accepted delivery, or process start if none since.",
                      "# TYPE camayoc_github_ingress_last_accepted_timestamp_seconds gauge",
                      f"camayoc_github_ingress_last_accepted_timestamp_seconds {self.last_accepted:.0f}"]
            data = ("\n".join(lines) + "\n").encode()
            tmp = self.path.with_name(self.path.name + ".tmp")
            try:
                try:
                    tmp.write_bytes(data)
                    os.replace(tmp, self.path)
                except OSError as exc:
                    # The scraped directory is not ours to create files in: rewrite
                    # the one pre-created file we may write, in a single write.
                    # Under ProtectSystem=strict the directory is a read-only mount
                    # (EROFS) with only the file bound writable, not EACCES.
                    if exc.errno not in (errno.EACCES, errno.EPERM, errno.EROFS):
                        raise
                    fd = os.open(self.path, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW)
                    try:
                        os.write(fd, data)
                    finally:
                        os.close(fd)
            except OSError as exc:  # metrics never break the receiver
                sys.stderr.write(f"github-ingress metrics write failed: {type(exc).__name__}\n")


class SeenBodies:
    """Digests of recently accepted signed bodies, bounded and expiring.

    The signature covers only the body, so a captured body re-sent under a
    fresh X-GitHub-Delivery would otherwise be spooled again. GitHub's own
    redelivery keeps the delivery id (409 above), so a known body under a new
    id is a replay too. In memory only: a restart forgets, and the drain's
    activity-key ledger still stops a second write."""

    def __init__(self, limit: int = MAX_PENDING, ttl: float = RETAIN_DAYS * 86400, clock=time.time):
        self.limit, self.ttl, self.clock = limit, ttl, clock
        self.seen: collections.OrderedDict[str, float] = collections.OrderedDict()
        self.lock = threading.Lock()

    def check_and_add(self, body: bytes) -> bool:
        """True when this body is new (and remembers it); False for a replay."""
        digest = hashlib.sha256(body).hexdigest()
        now = self.clock()
        with self.lock:
            while self.seen and (len(self.seen) > self.limit or next(iter(self.seen.values())) < now - self.ttl):
                self.seen.popitem(last=False)
            if digest in self.seen:
                return False
            self.seen[digest] = now
            return True

    def forget(self, body: bytes) -> None:
        with self.lock:
            self.seen.pop(hashlib.sha256(body).hexdigest(), None)


class LimitedServer(ThreadingHTTPServer):
    """At most MAX_CONCURRENT requests in flight; the rest are closed unanswered."""

    daemon_threads = True

    def __init__(self, address, handler, max_concurrent: int = MAX_CONCURRENT):
        self.slots = threading.BoundedSemaphore(max_concurrent)
        super().__init__(address, handler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class SourceAllowlist:
    """GitHub's webhook source ranges (api.github.com/meta "hooks"), read from a
    file a timer refreshes (`meta-refresh`), as a second layer behind HMAC.

    Behind Tailscale Funnel the TCP peer is always loopback; Funnel overwrites
    X-Forwarded-For with the real client address and marks the request with
    Tailscale-Funnel-Request. So the forwarded address is trusted ONLY from a
    loopback peer on a Funnel-marked request, and must be one bare address.
    Anything else has no provable origin and is refused. A missing, unreadable,
    empty or stale file refuses every request (fail closed)."""

    def __init__(self, path: Path, max_age: float = 2 * 86400, clock=time.time):
        self.path, self.max_age, self.clock = path, max_age, clock
        self.lock = threading.Lock()
        self._mtime: float | None = None
        self._nets: list = []
        self._fetched = 0.0

    def _load(self) -> None:
        try:
            mtime = self.path.stat().st_mtime
            if mtime == self._mtime:
                return
            doc = json.loads(self.path.read_text())
            nets = [ipaddress.ip_network(c, strict=False) for c in doc["hooks"]]
            fetched = float(doc["fetched_at"])
        except (OSError, ValueError, KeyError, TypeError):
            self._mtime, self._nets, self._fetched = None, [], 0.0
            return
        self._mtime, self._nets, self._fetched = mtime, nets, fetched

    def client(self, peer: str, headers) -> str | None:
        try:
            if not ipaddress.ip_address(peer).is_loopback:
                return peer
        except ValueError:
            return None
        if (headers.get_all("Tailscale-Funnel-Request") or []) != ["?1"]:
            return None
        forwarded = (headers.get_all("X-Forwarded-For") or [])
        if len(forwarded) != 1:
            return None
        return forwarded[0].strip()

    def verdict(self, peer: str, headers) -> str | None:
        """None when the source is allowed, else the refusal verdict."""
        with self.lock:
            self._load()
            age = self.clock() - self._fetched
            # A fetched_at in the future (clock skew, bad file) never ages out: refuse it.
            if not self._nets or age > self.max_age or age < -300:
                return "source_unknown"
            addr = self.client(peer, headers)
            try:
                ip = ipaddress.ip_address(addr) if addr else None
            except ValueError:
                ip = None
            if ip is None:
                return "source"
            return None if any(ip in n for n in self._nets if n.version == ip.version) else "source"


def refresh_meta(out: Path, *, fetch=None, clock=time.time) -> int:
    """Fetch api.github.com/meta and write {"fetched_at", "hooks"} atomically.
    Refuses (exit 2, file untouched) on any failure or an empty/invalid list,
    so a bad fetch never widens or empties the allowlist; staleness then
    fails the receiver closed."""
    import urllib.request
    if fetch is None:
        def fetch():
            req = urllib.request.Request("https://api.github.com/meta",
                                         headers={"Accept": "application/vnd.github+json",
                                                  "User-Agent": "camayoc-github-ingress"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read(1 << 20)
    try:
        hooks = json.loads(fetch())["hooks"]
        nets = [str(ipaddress.ip_network(c, strict=True)) for c in hooks]
    except Exception as exc:  # noqa: BLE001 - any failure leaves the old file in place
        sys.stderr.write(f"github-ingress meta-refresh refused: {type(exc).__name__}\n")
        return 2
    if not nets or not all(isinstance(c, str) for c in hooks):
        sys.stderr.write("github-ingress meta-refresh refused: empty hooks list\n")
        return 2
    data = json.dumps({"fetched_at": clock(), "hooks": nets}, sort_keys=True).encode()
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_bytes(data)
    os.chmod(tmp, 0o644)
    os.replace(tmp, out)
    print(json.dumps({"outcome": "refreshed", "hooks": len(nets)}))
    return 0


def make_handler(spool: Spool, secret: bytes, repos: set[str], path: str, wake: threading.Event, *,
                 events: set[str] | None = None, max_body: int | None = None,
                 limiter: RejectLimiter | None = None, metrics: Metrics | None = None,
                 deadline: float | None = None, bodies: SeenBodies | None = None,
                 sources: SourceAllowlist | None = None, recruitment_inbox: bool = False):
    """The webhook handler. `events` is the allowlist (None: the mapped events + ping).

    `deadline` bounds one whole connection (headers, body, answer) in wall-clock
    seconds. The socket timeout alone bounds each read, so a client trickling one
    byte per read could hold a slot forever. When the deadline passes, the socket
    is shut down, the request is counted as rejected and nothing is spooled."""
    if events is None:
        events = set(load_mapping()[0]["events"]) | {"ping"}
    cap = MAX_BODY if max_body is None else max_body
    limiter = limiter or RejectLimiter()
    metrics = metrics or Metrics()
    budget = REQUEST_DEADLINE if deadline is None else deadline
    bodies = bodies if bodies is not None else SeenBodies()

    class Handler(BaseHTTPRequestHandler):
        server_version = "camayoc-github-ingress"
        sys_version = ""
        timeout = 10  # a slow client cannot hold a thread

        def _send(self, code: int, payload: dict | None = None) -> None:
            data = b"" if payload is None else json.dumps(payload, sort_keys=True).encode()
            self.send_response_only(code)
            self.send_header("Content-Length", str(len(data)))
            if data:
                self.send_header("Content-Type", "application/json")
            if code >= 400:
                self.send_header("Connection", "close")
                self.close_connection = True
            self.end_headers()
            if data:
                self.wfile.write(data)

        def _verdict(self, verdict: str, code: int, event: str = "", delivery: str = "",
                     payload: dict | None = None) -> None:
            if self.expired:
                return  # cut by the deadline: handle() records one deadline verdict, no answer
            self.answered = True
            if code >= 400 and verdict != "rate_limited":
                limiter.spend()
            metrics.record(verdict)
            ev = event if _EVENT.fullmatch(event or "") else "-"
            dl = delivery if _DELIVERY.fullmatch(delivery or "") else "-"
            sys.stderr.write(f"github-ingress verdict={verdict} status={code} event={ev} delivery={dl}\n")
            self._send(code, payload)

        def setup(self):
            super().setup()
            self.expired = False
            self.answered = False
            self.watchdog = threading.Timer(budget, self._expire)
            self.watchdog.daemon = True
            self.watchdog.start()

        def _expire(self):
            self.expired = True
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        def handle(self):
            try:
                super().handle()
            except (OSError, ValueError):
                if not self.expired:
                    raise
            if self.expired and not self.answered:
                limiter.spend()
                metrics.record("deadline")
                sys.stderr.write("github-ingress verdict=deadline status=- event=- delivery=-\n")

        def finish(self):
            self.watchdog.cancel()
            try:
                super().finish()
            except OSError:
                pass

        def log_message(self, fmt, *args):  # one verdict line per request instead; never the request line
            pass

        def log_error(self, fmt, *args):
            pass

        def send_error(self, code, message=None, explain=None):  # stdlib parse errors: bare, counted
            self._verdict("malformed", code if code in (400, 408, 414, 431) else 400)

        def _refuse_method(self) -> None:
            if limiter.exhausted():
                return self._verdict("rate_limited", 429)
            if self.path == path:
                return self._verdict("method", 405)
            return self._verdict("path", 404)

        do_GET = do_HEAD = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_TRACE = do_CONNECT = _refuse_method

        def do_POST(self):
            if limiter.exhausted():
                return self._verdict("rate_limited", 429)
            if self.path != path:
                return self._verdict("path", 404)
            if sources is not None:
                refused = sources.verdict(self.client_address[0], self.headers)
                if refused:
                    return self._verdict(refused, 403)
            if len(self.headers) > MAX_HEADERS:
                return self._verdict("headers", 431)
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                return self._verdict("length", 411)
            if length < 0 or length > cap:
                return self._verdict("too_large", 413)
            body = self.rfile.read(length)
            if len(body) != length:
                return self._verdict("short_body", 400)
            if not verify_signature(secret, self.headers.get("X-Hub-Signature-256"), body):
                return self._verdict("signature", 401)
            event = self.headers.get("X-GitHub-Event", "")
            delivery = self.headers.get("X-GitHub-Delivery", "")
            if not _EVENT.fullmatch(event) or not _DELIVERY.fullmatch(delivery):
                return self._verdict("headers", 400)
            if event not in events:
                return self._verdict("event", 400, event, delivery)
            if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
                return self._verdict("content_type", 415, event, delivery)
            try:
                payload = json.loads(body)
            except ValueError:
                return self._verdict("json", 400, event, delivery)
            if event == "ping":
                return self._verdict("ping", 200, event, delivery, {"outcome": "pong"})
            full = (payload.get("repository") or {}).get("full_name") if isinstance(payload, dict) else None
            if full not in repos:
                return self._verdict("ignored", 202, event, delivery,
                                     {"outcome": "ignored", "reason": "repository not watched"})
            if (spool.pending() >= MAX_PENDING or recruitment_inbox and
                    spool.recruitment_pending() >= MAX_PENDING):
                return self._verdict("spool_full", 503, event, delivery)
            if self.expired:
                return  # the deadline already closed this connection; spool nothing
            if spool.has(delivery):
                # A validly signed delivery id seen before is a replay: refused, never re-spooled.
                return self._verdict("replay", 409, event, delivery)
            if not bodies.check_and_add(body):
                # The same signed body under a fresh delivery id: a captured replay.
                return self._verdict("replay_body", 409, event, delivery)
            outcome = spool.add(delivery, event, payload, verified_webhook=recruitment_inbox)
            if outcome != "accepted":
                bodies.forget(body)
                return self._verdict("replay", 409, event, delivery)
            wake.set()
            self._verdict("accepted", 202, event, delivery, {"outcome": outcome, "delivery": delivery})

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
                 reserve: int = 1000, mapping: tuple[dict, str] | None = None,
                 observe_mergeability: bool = False):
        self.spool, self.transport, self.repos = spool, transport, repos
        self.budget, self.reserve = budget_per_hour, reserve
        self.mapping, self.sha = mapping or load_mapping()
        self.poll_interval = 60
        self.observe_mergeability = observe_mergeability

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
        if event != "pull_request" or payload.get("action") == "observed":
            # A sighting describes the PR as fetched, so its fetched state stands.
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
        self._sweep_open_prs(full, repo, now, receipt)
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


    def _sweep_open_prs(self, full: str, repo: dict, now: float, receipt: dict) -> None:
        """Record every open PR, including one with no recent event (aegis-xxoa.5).

        The Events API only reaches the last ~100 events, so a quiet open PR is
        never seen there. One ETag'd list call per repo per cycle spools each open
        PR as a `pull_request.observed` sighting, deduplicated on its node id,
        head, state and update time. A PR that was open at the last full sweep and
        is missing now is fetched once (hydration) and recorded as it is: closed,
        merged or still open. A list of a full page is TRUNCATED: it adds sightings
        but never lets an absence count, so no PR is ever presumed closed."""
        page = self._json(f"/repos/{full}/pulls?state=open&per_page={OPEN_PAGE}", now, receipt,
                          etag_key=f"pulls-open:{full}")
        if page == "304" and self.observe_mergeability:
            # Base changes need not invalidate the open-list ETag. Detail
            # checks use retained identities and the same budget and backoff.
            for node, (_, number, _) in self.spool.open_prs(full).items():
                self._observe_pr(full, repo, node, number, now, receipt)
            return
        if not isinstance(page, list):
            return  # 304 (unchanged), or an error already in the receipt
        seen: dict[str, tuple] = {}
        complete = True
        for pr in page:
            if not isinstance(pr, dict):
                receipt["malformed_prs"] = receipt.get("malformed_prs", 0) + 1
                complete = False
                continue
            node, url = pr.get("node_id"), pr.get("url")
            if not isinstance(node, str) or not isinstance(url, str):
                complete = False
                continue
            if node in seen:
                complete = False
                continue  # a duplicate page row cannot establish a complete sweep
            seen[node] = (url, pr.get("number"), pr.get("updated_at"))
            if self.observe_mergeability:
                self._observe_pr(full, repo, node, pr.get("number"), now, receipt)
                continue
            head = (pr.get("head") or {}).get("sha")
            delivery = f"poll:pr-open:{node}:{head}:{pr.get('updated_at')}"
            if not self.spool.has(delivery):
                payload = {"action": "observed", "pull_request": pr, "repository": repo, "sender": None}
                self._spool(delivery, "pull_request", payload, now, receipt)
        if not complete or len(page) >= OPEN_PAGE:
            receipt.setdefault("truncated", []).append(full)
            return
        for node, (url, number, updated) in self.spool.open_prs(full).items():
            if node in seen:
                continue
            delivery = f"poll:pr-gone:{node}:{updated}"
            if not self.spool.has(delivery):
                trimmed = {"node_id": node, "url": url, "number": number}
                payload = {"action": "observed", "pull_request": trimmed, "repository": repo,
                           "sender": None, "_hydrate": {"pull_request": url}}
                self._spool(delivery, "pull_request", payload, now, receipt)
        self.spool.set_open_prs(full, seen)


    def _observe_pr(self, full, repo, node, number, now, receipt):
        if type(number) is not int or number < 1:
            receipt["malformed_prs"] = receipt.get("malformed_prs", 0) + 1
            return
        pr = self._json(f"/repos/{full}/pulls/{number}", now, receipt)
        if (not isinstance(pr, dict) or pr.get("node_id") != node
                or pr.get("number") != number):
            receipt.setdefault("errors", []).append(f"{full}#{number}: PR detail identity unknown")
            return
        head, base = pr.get("head"), pr.get("base")
        if not isinstance(head, dict) or not isinstance(base, dict):
            receipt.setdefault("errors", []).append(f"{full}#{number}: head/base unknown")
            return
        state_key = _digest(head.get("sha"), base.get("sha"), base.get("ref"), pr.get("state"),
                            pr.get("merged"), pr.get("updated_at"), pr.get("draft"),
                            pr.get("mergeable"), pr.get("mergeable_state"))
        payload = {"action": "observed", "pull_request": pr, "repository": repo, "sender": None}
        outcome = self.spool.observe_pr(full, node, state_key, payload, now)
        receipt[outcome] = receipt.get(outcome, 0) + 1


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
    s.add_argument("--recruitment-inbox", action="store_true",
                   help="append verified webhook recruitment wakes (off until separate drain is installed)")
    s.add_argument("--no-drain", action="store_true",
                   help="receive and spool only; hold no Quipu credential (run `drain --loop` separately)")
    s.add_argument("--max-body", type=int, default=MAX_BODY, help="bytes; larger bodies get 413 unread")
    s.add_argument("--max-concurrent", type=int, default=MAX_CONCURRENT)
    s.add_argument("--request-deadline", type=float, default=REQUEST_DEADLINE,
                   help="seconds for one whole request (headers, body, answer)")
    s.add_argument("--metrics-file", type=Path, help="node_exporter textfile for verdict counters")
    s.add_argument("--source-allowlist", type=Path,
                   help="meta-refresh output; refuse (403) any request not from GitHub's hook ranges")
    s.add_argument("--source-max-age", type=float, default=2 * 86400,
                   help="seconds; an older allowlist refuses every request")
    mr = sub.add_parser("meta-refresh", help="write GitHub's webhook source ranges for --source-allowlist")
    mr.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("poll", help="budgeted poller + drain")
    p.add_argument("--budget-per-hour", type=int, default=300)
    p.add_argument("--reserve", type=int, default=1000)
    p.add_argument("--interval", type=int, default=60)
    p.add_argument("--once", action="store_true")
    p.add_argument("--observe-mergeability", action="store_true",
                   help="read PR detail snapshots through the existing request budget")
    for cmd in (s, p):
        cmd.add_argument("--spool", required=True)
        cmd.add_argument("--repo", action="append", required=True, help="owner/name; repeat")
        cmd.add_argument("--actor", required=True)
        cmd.add_argument("--dry-run", action="store_true", help="print episodes instead of posting")
        cmd.add_argument("--retain-days", type=float, default=RETAIN_DAYS)
    d = sub.add_parser("drain", help="one drain pass over the spool, or a loop of them")
    d.add_argument("--spool", required=True)
    d.add_argument("--actor", required=True)
    d.add_argument("--post", action="store_true")
    d.add_argument("--repo", action="append", help="owner/name; repeat (default: every repository)")
    d.add_argument("--loop", type=float, metavar="SECONDS",
                   help="keep draining every SECONDS, purging past --retain-days")
    d.add_argument("--retain-days", type=float, default=RETAIN_DAYS)
    for cmd in (s, p, d):
        cmd.add_argument("--spool-group-shared", action="store_true",
                         help="spool files 0660, for a receiver and a drain that run as different users")
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
    if args.cmd == "meta-refresh":
        return refresh_meta(args.out)
    if args.cmd == "status":
        print(json.dumps(Spool(args.spool).counts(), sort_keys=True))
        return 0
    if args.cmd == "drain":
        for full in args.repo or []:
            if not _REPO.match(full):
                ap.error(f"--repo {full!r} is not owner/name")
        spool = Spool(args.spool, group_shared=args.spool_group_shared)
        writer = Writer() if args.post else print_writer
        repos = set(args.repo) if args.repo else None
        while True:
            print(json.dumps(drain_once(spool, actor=args.actor, writer=writer, repos=repos),
                             sort_keys=True), flush=True)
            if args.loop is None:
                return 0
            spool.purge(args.retain_days, time.time())
            time.sleep(max(args.loop, 1.0))

    for full in args.repo:
        if not _REPO.match(full):
            ap.error(f"--repo {full!r} is not owner/name")
    spool = Spool(args.spool, group_shared=args.spool_group_shared)
    repos = set(args.repo)
    writer = print_writer if args.dry_run else Writer()
    wake, stop = threading.Event(), threading.Event()
    if args.cmd == "serve":
        secret = read_secret(args.secret_file)
        if not secret:
            sys.stderr.write("github-ingress: secret file empty or unreadable; every delivery will be refused\n")
        host, _, port = args.listen.rpartition(":")
        if args.max_body < 1 or args.max_concurrent < 1 or not 0 < args.request_deadline <= 60:
            ap.error("--max-body and --max-concurrent must be positive; --request-deadline in (0, 60]")
        metrics = Metrics(args.metrics_file)
        metrics.record("started")
        server = LimitedServer((host or "127.0.0.1", int(port)),
                               make_handler(spool, secret, repos, args.path, wake, max_body=args.max_body,
                                            metrics=metrics, deadline=args.request_deadline,
                                            recruitment_inbox=args.recruitment_inbox,
                                            sources=(SourceAllowlist(args.source_allowlist, args.source_max_age)
                                                     if args.source_allowlist else None)),
                               max_concurrent=args.max_concurrent)
        if not args.no_drain:
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
    poller = Poller(spool, GhTransport(), args.repo, budget_per_hour=args.budget_per_hour,
                    reserve=args.reserve, observe_mergeability=args.observe_mergeability)
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
