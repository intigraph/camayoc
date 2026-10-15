#!/usr/bin/env python3
"""Link every archived agent transcript to its Session in the observed plane.

    Principal  <--aegis:actor--  Session  --aegis:transcript-->  Artifact
                                     |
                                     +--aegis:declaredWorkItem-->  WorkItem   (st task boundaries)
                                                                  aegis:reference  "garage-hla:hla/agent-transcripts/<agent>/<file>"
                                                                  aegis:contentSha256

THE GAP THIS CLOSES (aegis-9lri74)
==================================

`aegis:Session` was only ever written by the cost path (publish_work_cost.py
inside the per-minute `st cost --sync`), which samples ACTIVE sessions. A
session that finished between samples never got a node. Measured 2026-09-25:
338 local session logs modified since 09-02, 7 with a Session node (~2%).

The transcript archive already holds every finished session: st-history-corpus
projects each harness log to `<corpus>/<agent>/<id>.md` and the publication
cron mirrors that tree to `garage-hla:hla/agent-transcripts/` (rsync, then
rclone sync, so the object key IS the relative path). This producer reads the
same local tree, so the pointer it writes names the published object.

A POINTER, NEVER THE CONTENT
============================

Only the frontmatter is parsed. The body is hashed and never read into a fact:
the Artifact carries the object key and the sha256 of the bytes published,
so a reader can fetch and verify it with the archive's own credentials.

SHARED TRIPLES WITH THE COST PRODUCER
=====================================

Both producers write `<session> a aegis:Session`, `aegis:actor`, `aegis:sourceKind`
and the label `"<harness> session <id>"` (the governed Session shape requires
exactly one label, so it must be the cost producer's BYTE-IDENTICAL string). quipu
retracts a replaced snapshot by logical (e, a, v), so a triple shared by two
sources survives only because EVERY replacing producer re-emits it in the
same transaction. Both do, on every snapshot. Nothing else is shared: every
transcript fact lives on the Artifact or on a predicate only this producer
writes.

DECLARED WORK ITEMS, NOT PROVEN ONES
====================================

`st agent stats --begin-task <bead>` records a paired, session-local task
boundary in st's stats.sqlite (`task_contexts`). Each paired start becomes
`aegis:declaredWorkItem`. The name is deliberate: st itself says a scope is a
declaration, not proof that it covers every action. A session with no
declaration gets no edge, which reads as UNKNOWN, never as "worked on nothing".

QUIESCENCE
==========

A live session's projection changes every corpus cycle. A session is linked
once its record has been unchanged for --quiet-minutes; a resumed session
changes digest and its snapshot is replaced, never duplicated.

Usage:
    python3 scripts/ingest_transcript_sessions.py --corpus <dir> --state <f>            # dry run
    python3 scripts/ingest_transcript_sessions.py --corpus <dir> --state <f> --post
    python3 scripts/ingest_transcript_sessions.py --corpus <dir> --state <f> --check-liveness
"""

from __future__ import annotations

import argparse
import base64
import fcntl
import os
import datetime
import hashlib
import json
import re
import sqlite3
import sys
import time
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
import planes  # noqa: E402
from ingest_session_usage import BASE, ONTOLOGY, esc  # noqa: E402

REMOTE = "garage-hla:hla/agent-transcripts"
SCHEMA = "agent-transcript/v1"
AGENT = re.compile(r"[a-z][a-z0-9_]{0,31}")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
CLIENT = "camayoc-transcripts"
SHAPES = Path(__file__).resolve().parents[1] / "shapes/session-transcript.shapes.ttl"


def frontmatter(path: Path) -> dict | None:
    with path.open("rb") as handle:
        head = handle.read(2048).decode(errors="replace")
    if not head.startswith("---\n") or "\n---\n" not in head[4:]:
        return None
    block = head[4:].split("\n---\n", 1)[0]
    return dict(re.findall(r"^(\w+): (.*)$", block, re.M))


def session_id(meta: dict) -> str | None:
    """The id the harness itself records, so both producers mint one IRI.

    claude: the corpus id IS the log's sessionId. codex: the corpus id is the
    rollout stem, whose trailing UUID is session_meta.id — the value
    ingest_session_usage.read_codex keys the cost Session by.
    """
    raw = meta.get("id", "")
    if meta.get("harness") == "claude":
        return raw if UUID.fullmatch(raw) else None
    if meta.get("harness") == "codex" and raw.startswith("rollout-"):
        found = UUID.findall(raw)
        return found[-1] if found else None
    return None


def excluded(path: Path) -> set[str]:
    """The publisher's own rsync exclude list, as corpus-relative paths.

    A file on it has NO Garage object, so a pointer to it would name nothing.
    Its absence is refused rather than read as "nothing excluded": this
    producer cannot tell published from unpublished without it.
    """
    return {line.strip().lstrip("/") for line in path.read_text().splitlines() if line.strip()}


TASK = re.compile(r"[A-Za-z][A-Za-z0-9_]*-[A-Za-z0-9][A-Za-z0-9_.-]{0,95}")


def declared_tasks(stats_db: Path | None) -> dict[str, list[str]]:
    """Paired task boundaries per session, read-only. None means not asked."""
    if stats_db is None:
        return {}
    with sqlite3.connect(f"file:{stats_db}?mode=ro", uri=True) as conn:
        rows = conn.execute("SELECT session, task FROM task_contexts WHERE paired_start = 1").fetchall()
    tasks: dict[str, set[str]] = {}
    for session, task in rows:
        if TASK.fullmatch(task or ""):
            tasks.setdefault(session, set()).add(task)
    return {session: sorted(found) for session, found in tasks.items()}


def digest(record: dict) -> str:
    # Transcript-only sessions keep the bare sha, so adding declarations
    # re-publishes only the sessions that have them.
    tasks = record.get("tasks") or []
    return record["sha256"] + (":" + ",".join(tasks) if tasks else "")


def scan(corpus: Path, quiet_seconds: float, now: float,
         unpublished: set[str] = frozenset(),
         tasks: dict[str, list[str]] | None = None) -> tuple[list[dict], dict]:
    records, stats = [], {"files": 0, "linked": 0, "active": 0, "skipped": {}}

    def skip(reason):
        stats["skipped"][reason] = stats["skipped"].get(reason, 0) + 1

    for path in sorted(corpus.glob("*/*.md")):
        stats["files"] += 1
        meta = frontmatter(path)
        if not meta or meta.get("schema") != SCHEMA:
            skip("not agent-transcript/v1")
            continue
        agent = path.parent.name
        if f"{agent}/{path.name}" in unpublished:
            skip("excluded from publication")
            continue
        if meta.get("agent") != agent or not AGENT.fullmatch(agent):
            # A scratch directory is not a crew principal; attributing its
            # session to one would be worse than leaving it unlinked.
            skip("agent not a crew principal")
            continue
        sid = session_id(meta)
        if not sid:
            skip("no harness session id")
            continue
        if now - path.stat().st_mtime < quiet_seconds:
            stats["active"] += 1
            continue
        data = path.read_bytes()
        records.append({
            "session": sid, "agent": agent, "harness": meta["harness"],
            "at": meta.get("timestamp", ""), "key": f"{REMOTE}/{agent}/{path.name}",
            "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
            "tasks": (tasks or {}).get(sid, []),
        })
        stats["linked"] += 1
    return records, stats


def iris(record: dict) -> tuple[str, str]:
    session = f"{BASE}session/{quote(record['session'], safe='')}"
    return session, f"{session}/transcript"


def turtle(record: dict) -> str:
    session, artifact = iris(record)
    o = ONTOLOGY
    out = [
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
        "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .",
        "",
        f"<{session}> a <{o}Session> ;",
        # Same string as ingest_session_usage.emit: one shared triple, not two labels.
        f'    rdfs:label "{esc(record["harness"])} session {esc(record["session"])}" ;',
        f'    <{o}sourceKind> "observed" ;',
        f"    <{o}actor> <{BASE}principal/{quote(record['agent'], safe='')}> ;",
        f'    <{o}sessionHarness> "{esc(record["harness"])}" ;',
        f"    <{o}transcript> <{artifact}> .",
        "",
        f"<{artifact}> a <{o}Artifact> ;",
        f'    rdfs:label "{esc(record["harness"])} transcript {esc(record["session"])} ({esc(record["agent"])})" ;',
        f'    <{o}reference> "{esc(record["key"])}" ;',
        f'    <{o}contentSha256> "{record["sha256"]}" ;',
        f"    <{o}contentBytes> {record['bytes']} ;",
        f'    <{o}sourceKind> "observed" ;',
        f"    <{o}transcriptOf> <{session}> .",
    ]
    for task in record.get("tasks") or []:
        out.append(f"<{session}> <{o}declaredWorkItem> <{o}{task}> .")
    if record["at"]:
        out.append(f'<{session}> <{o}lastActivityAt> "{esc(record["at"])}"^^xsd:dateTime .')
    return "\n".join(out) + "\n"


def body(record: dict, actor: str) -> dict:
    snapshot = f"camayoc-session-transcript:{record['harness']}:{record['session']}"
    return {"turtle": turtle(record), "actor": actor, "source": snapshot,
            "graph": planes.plane_for("observed"), "replace_snapshot": True,
            "snapshot": snapshot, "shapes": SHAPES.read_text()}


def query(sparql: str) -> list:
    rows = planes._post("/query", {"query": sparql, "graph": planes.plane_for("observed")},
                        client=CLIENT).get("rows")
    if not isinstance(rows, list):
        raise ValueError("INDETERMINATE: query returned no rows field")
    return rows


def read_back(record: dict) -> bool:
    _, artifact = iris(record)
    rows = query(f'SELECT ?h WHERE {{ <{artifact}> <{ONTOLOGY}contentSha256> ?h }}')
    return any(str(r.get("h", "")).strip('"') == record["sha256"] for r in rows)


def canonical_payload(payload: dict) -> bytes:
    # This is planes._post's wire serialization; validate it again on recovery.
    return json.dumps(payload, sort_keys=True).encode()


def durable_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def pending_envelope(record: dict, payload: dict) -> dict:
    raw = canonical_payload(payload)
    return dict(version=1, record=record, payload_b64=base64.b64encode(raw).decode(),
                payload_sha256=hashlib.sha256(raw).hexdigest(), payload_bytes=len(raw),
                tasks=record.get("tasks") or [], created_at=time.time())


def retained_payload(envelope: dict) -> tuple[dict, dict]:
    try:
        raw = base64.b64decode(envelope["payload_b64"], validate=True)
        payload = json.loads(raw)
        record = envelope["record"]
        if (envelope["version"] != 1 or len(raw) != envelope["payload_bytes"] or
                hashlib.sha256(raw).hexdigest() != envelope["payload_sha256"] or
                canonical_payload(payload) != raw or
                envelope["tasks"] != (record.get("tasks") or []) or
                not isinstance(envelope["created_at"], (float, int)) or
                payload["turtle"] != turtle(record) or
                payload["snapshot"] != f"camayoc-session-transcript:{record['harness']}:{record['session']}" or
                payload["graph"] != planes.plane_for("observed") or
                payload["replace_snapshot"] is not True):
            raise ValueError("inconsistent pending payload")
        return record, payload
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("pending journal legacy or invalid; adjudication required") from exc


def snapshot_outcome(record: dict) -> str:
    session, artifact = iris(record)
    # These predicates belong to this producer. An absent hash alone is not
    # absence: partial snapshots and changed task sets must refuse replacement.
    rows = query(f'SELECT ?s ?p ?o WHERE {{ VALUES ?s {{ <{session}> <{artifact}> }} '
                 f'?s ?p ?o . FILTER(?p = <{ONTOLOGY}contentSha256> || '
                 f'?p = <{ONTOLOGY}contentBytes> || ?p = <{ONTOLOGY}reference> || '
                 f'?p = <{ONTOLOGY}declaredWorkItem> || ?p = <{ONTOLOGY}transcript> || '
                 f'?p = <{ONTOLOGY}transcriptOf> || ?p = <{ONTOLOGY}lastActivityAt>) }}')
    if not rows:
        control = query(f"SELECT ?s WHERE {{ ?s a <{ONTOLOGY}Session> }} LIMIT 1")
        if not control:
            raise ValueError("pending absence has no positive records-plane control")
        return "absent"
    actual = {(str(r["s"]), str(r["p"]), str(r["o"]).strip('"')) for r in rows}
    expected = {(artifact, ONTOLOGY + "contentSha256", record["sha256"]),
                (artifact, ONTOLOGY + "contentBytes", str(record["bytes"])),
                (artifact, ONTOLOGY + "reference", record["key"]),
                (session, ONTOLOGY + "transcript", artifact),
                (artifact, ONTOLOGY + "transcriptOf", session)}
    if record.get("at"):
        expected.add((session, ONTOLOGY + "lastActivityAt", record["at"]))
    expected.update((session, ONTOLOGY + "declaredWorkItem", ONTOLOGY + task)
                    for task in record.get("tasks") or [])
    return "landed" if actual == expected else "superseded"


def finish_pending(state: dict, state_path: Path, marker: Path, record: dict) -> None:
    state[record["session"]] = digest(record)
    durable_json(state_path, state)
    marker.unlink()


def send_pending(envelope: dict, state: dict, state_path: Path, marker: Path) -> dict:
    record, payload = retained_payload(envelope)
    response = planes._post("/knot", payload, client=CLIENT)
    if response.get("conforms") is False:
        # Keep the exact refused payload as evidence, never treat refusal as landed.
        raise ValueError("graph refused retained transcript snapshot; adjudication required")
    if not response.get("tx_id") or snapshot_outcome(record) != "landed":
        raise ValueError("snapshot accepted but complete read-back unproven")
    finish_pending(state, state_path, marker, record)
    return response


def publish(records: list[dict], actor: str, state_path: Path, limit: int) -> dict:
    if limit < 1:
        raise ValueError("publication limit must be positive")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with state_path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return publish_locked(records, actor, state_path, limit)


def publish_locked(records: list[dict], actor: str, state_path: Path, limit: int) -> dict:
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    marker = state_path.with_suffix(".pending.json")
    recovered = 0
    if marker.exists():
        envelope = json.loads(marker.read_text())
        record, _ = retained_payload(envelope)
        outcome = snapshot_outcome(record)
        if outcome == "landed":
            finish_pending(state, state_path, marker, record)
        elif outcome == "absent":
            time.sleep(1.0)
            if snapshot_outcome(record) != "absent":
                raise ValueError("pending snapshot changed between reads; adjudication required")
            # No current-corpus rebuild: replay the retained original bytes only.
            send_pending(envelope, state, state_path, marker)
        else:
            raise ValueError("pending snapshot superseded; archive by explicit adjudication, no replay")
        recovered = 1
    pending = [r for r in records if state.get(r["session"]) != digest(r)]
    receipt = {"pending": len(pending), "posted": 0, "tx": [], "reconciled": recovered}
    for record in pending[:limit]:
        envelope = pending_envelope(record, body(record, actor))
        durable_json(marker, envelope)
        started = time.monotonic()
        response = send_pending(envelope, state, state_path, marker)
        receipt["posted"] += 1
        receipt["tx"].append(response["tx_id"])
        time.sleep(max(0.0, 1.0 - (time.monotonic() - started)))
    receipt["remaining"] = len(pending) - receipt["posted"]
    return receipt


def archive_pending(state_path: Path, reason_path: Path) -> dict:
    """Explicit adjudication only; archives exact bytes and never publishes."""
    reason = reason_path.read_text().strip()
    if not reason or len(reason.encode()) > 16384:
        raise ValueError("pending archive needs a bounded explicit adjudication reason")
    marker = state_path.with_suffix(".pending.json")
    with state_path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        raw = marker.read_bytes()
        key = hashlib.sha256(raw).hexdigest()
        directory = state_path.parent / (state_path.stem + ".pending-archive")
        directory.mkdir(mode=0o700, exist_ok=True)
        target = directory / (key + ".json")
        # No overwrite: a prior archive must already contain these exact bytes.
        try:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            if target.read_bytes() != raw:
                raise ValueError("pending archive collision")
        else:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        durable_json(directory / (key + ".reason.json"),
                     dict(reason=reason, archived_at=time.time(), sha256=key, bytes=len(raw)))
        if target.read_bytes() != raw or marker.read_bytes() != raw:
            raise ValueError("pending archive verification failed")
        marker.unlink()
        return dict(outcome="explicitly-superseded", sha256=key, bytes=len(raw))


def liveness(records: list[dict], lag_seconds: float, now: float) -> tuple[int, str]:
    """OUTPUT, not process-up: is the newest due transcript linked in the graph?

    0 linked, 1 stale (a due transcript is absent), 2 UNKNOWN (control failed
    or nothing is due). A process probe cannot see the failure this exists
    for: the cost path stayed up while finished sessions never got a node.
    """
    control = query(f"SELECT ?s WHERE {{ ?s a ?t . FILTER(?t = <{ONTOLOGY}Session>) }} LIMIT 1")
    if not control:
        return 2, "UNKNOWN: control query found no Session in the observed plane"
    due = [r for r in records if r["at"] and
           now - datetime.datetime.fromisoformat(r["at"].replace("Z", "+00:00")).timestamp() >= lag_seconds]
    if not due:
        return 2, "UNKNOWN: no transcript old enough to be due"
    newest = max(due, key=lambda r: r["at"])
    _, artifact = iris(newest)
    rows = query(f'SELECT ?h WHERE {{ <{artifact}> <{ONTOLOGY}contentSha256> ?h }}')
    if not rows:
        return 1, f"STALE: newest due transcript {newest['key']} ({newest['at']}) has no Artifact"
    return 0, f"LIVE: newest due transcript {newest['key']} ({newest['at']}) is linked"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--corpus", type=Path, required=True)
    ap.add_argument("--state", type=Path, required=True)
    ap.add_argument("--exclude-file", type=Path,
                    default=Path.home() / ".local/state/aegis/snapshot-publication/exclude.txt",
                    help="the publication gate's rsync exclude list (required to exist)")
    ap.add_argument("--stats-db", type=Path,
                    help="st stats.sqlite; its paired task boundaries become declaredWorkItem edges")
    ap.add_argument("--actor", default="camayoc-transcripts")
    ap.add_argument("--quiet-minutes", type=float, default=60)
    ap.add_argument("--limit", type=int, default=40,
                    help="snapshots per run; a backfill is partitioned across runs")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--post", action="store_true")
    mode.add_argument("--check-liveness", action="store_true")
    mode.add_argument("--archive-pending", type=Path, metavar="REASON_FILE",
                      help="explicit owner adjudication; archive exact pending bytes, never post")
    ap.add_argument("--lag-minutes", type=float, default=180,
                    help="liveness: a transcript older than this must be linked")
    args = ap.parse_args()
    now = time.time()
    try:
        if args.archive_pending:
            print(json.dumps(archive_pending(args.state, args.archive_pending), sort_keys=True))
            return 0
        records, stats = scan(args.corpus, args.quiet_minutes * 60, now,
                              excluded(args.exclude_file), declared_tasks(args.stats_db))
        # THE DENOMINATOR, always: a run that linked a tenth of the archive
        # must not read like one that linked all of it.
        print(json.dumps({"scan": stats}), file=sys.stderr)
        if args.check_liveness:
            code, message = liveness(records, args.lag_minutes * 60, now)
            print(message)
            return code
        if args.post:
            print(json.dumps(publish(records, args.actor, args.state, args.limit), sort_keys=True))
            return 0
        for record in records[: args.limit]:
            print(turtle(record))
        return 0
    except (OSError, ValueError, planes.PlaneError) as exc:
        print(f"transcript linkage UNKNOWN: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
