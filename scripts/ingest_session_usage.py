#!/usr/bin/env python3
"""Parse harness session logs into the §D cost vocabulary as Turtle.

    Principal  <--aegis:actor--  Session  <--aegis:inSession--  UsageRecord

THE GAP THIS CLOSES
===================

`docs/design/incident-corpus.md` §5 item 3 asks for agent-hour/agent-token
normalisation "available from the cost-accounting slice (competency §D, Q17)
— both harnesses already write complete per-session token accounting to local
disk, so agent-hours are a deterministic-parser fact and need no new
plumbing." `camayoc-e29` minted the vocabulary and the stored queries
(Q16–Q21) and proved them against a fixture. Nothing turned a live window's
records into that vocabulary, so every one of those queries answered only
about the fixture.

DETERMINISTIC, WHICH IS THE WHOLE POINT
=======================================

Ingress rule 3: a parser can produce these from a record, so a parser does,
and they enter as `observed` with no model in the loop. Run it twice over the
same logs and you get byte-identical Turtle.

THE UNIT OF CONSUMPTION IS THE REQUEST, NOT THE LOG ENTRY
=========================================================

MEASURED, NOT REASONED. The claude harness writes one JSONL entry per content
block of an assistant turn — thinking, text, and each tool call — and **every
one of them repeats the same `usage` object for the whole request**. Measured
against a real 2026-08-24 session log: 237 entries carrying usage, 92 distinct
`requestId`s, and the usage object identical across every entry sharing a
requestId. Summing per entry reports 39,467,766 tokens where the session
consumed 15,653,391 — a 2.5x overcount, in the flattering direction for
anyone reporting throughput and the punishing one for anyone reporting spend.

The record is keyed by `requestId`; identical repeats are deduplicated and
conflicting counts are abstained. An
entry carrying usage but **no** requestId cannot be deduplicated against its
siblings and is therefore ABSTAINED — counted in the summary, never emitted.
A dropped request understates a total visibly; a triple-counted one corrupts
every rate built on it.

MEASURED CODEX ACCOUNTING
========================

Codex response records are verified against real rollout thread-ledger deltas.
They include compaction requests that legacy token_count snapshots omit. Unknown
models or breakdown fields remain unknown; conflicting repeated identities are
abstained instead of accepting whichever row happened to arrive first.

Q19 IS WHY EVERY SESSION IS EMITTED
===================================

`aegis:Session` is written for every recognised log, whether or not any usage
survived. "Which sessions carry NO usage record?" needs the session stored
independently of its records, so that a missing measurement reads UNKNOWN
rather than aggregating as zero. A parser that emitted only sessions it could
cost would make that question unanswerable by construction.

ONE QUANTITY, NOT THE BREAKDOWN
===============================

`aegis:tokensConsumed` is total consumption: input + cache-creation +
cache-read + output. The reader also retains the disjoint breakdown and model for per-work
retrieval; the legacy Turtle interface retains its existing total vocabulary. Consumption only — never remaining quota, which comes
from a different source and whose conflation with consumption cost this crew
a closed investigation (§D's own note).

Usage:
    python3 scripts/ingest_session_usage.py ~/.claude/projects/<slug> \\
        --principal strider > usage.ttl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import quote

BASE = "http://aegis.gastown.local/cost/"
ONTOLOGY = "http://aegis.gastown.local/ontology/"


KINDS = ("input_uncached", "cache_read_input", "cache_write_input", "output")


def _integer(value):
    return type(value) is int and value >= 0


def _counts(usage, fmt):
    """Disjoint quantities; absent fields stay unknown, not free consumption."""
    if not isinstance(usage, dict):
        return None
    inp, out = usage.get("input_tokens"), usage.get("output_tokens")
    if not _integer(inp) or not _integer(out):
        return None
    read = usage.get("cached_input_tokens" if fmt == "codex" else "cache_read_input_tokens")
    write = usage.get("cache_write_input_tokens" if fmt == "codex" else "cache_creation_input_tokens")
    if any(v is not None and not _integer(v) for v in (read, write)):
        return None
    if fmt == "codex":
        if read is not None and write is not None and read + write > inp:
            return None
        uncached = inp - read - write if read is not None and write is not None else None
        total = inp + out
    else:
        uncached = inp
        total = inp + out + read + write if read is not None and write is not None else None
    return dict(zip(KINDS, (uncached, read, write, out))), total


def _total(usage):
    result = _counts(usage, "claude")
    return result[1] if result else None


def pricing_metadata(usage):
    """Retain explicit modifiers; missing fields must not become cheap defaults."""
    return {key: usage[key] for key in ('speed', 'service_tier', 'inference_geo') if key in usage}


def _rows(path):
    # A partial trailing write is unknown evidence; it is counted by each reader.
    for line in path.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
            yield value if isinstance(value, dict) else None
        except ValueError:
            yield None


def _put(requests, conflicts, record):
    key = record["id"]
    if key in conflicts:
        return
    previous = requests.get(key)
    if previous:
        # Content blocks repeat counts but can have different receipt timestamps.
        fields = ("tokens", "counts", "model", "model_source", "detail")
        if any(previous[k] != record[k] for k in fields):
            conflicts.add(key)
            del requests[key]
        elif record["at"] < previous["at"]:
            requests[key] = record
    else:
        requests[key] = record


def read_claude(path):
    session_id, requests, conflicts, abstained = None, {}, set(), 0
    for entry in _rows(path):
        if entry is None:
            abstained += 1
            continue
        if "sessionId" not in entry:
            continue
        sid = entry.get("sessionId")
        if not sid:
            continue
        if session_id and session_id != sid:
            raise ValueError("mixed session identities in one transcript")
        session_id = sid
        message = entry.get("message") or {}
        usage = message.get("usage")
        if not isinstance(usage, dict):
            continue
        key, stamp = entry.get("requestId"), entry.get("timestamp")
        parsed = _counts(usage, "claude")
        if not key or not stamp or not parsed or parsed[1] is None:
            abstained += 1
            continue
        _put(requests, conflicts, {"id": key, "tokens": parsed[1], "at": stamp,
             "counts": parsed[0], "model": message.get("model"),
             "model_source": "message.model", "harness": "claude",
             "detail": {"cache_creation": usage.get("cache_creation"),
                        **({'pricing': pricing_metadata(usage)} if pricing_metadata(usage) else {}),
                        "reasoning_output": (usage.get("output_tokens_details") or {}).get("thinking_tokens")}})
    if not session_id:
        return None
    return session_id, sorted(requests.values(), key=lambda r: (r["at"], r["id"])), abstained + len(conflicts)


def read_codex(path):
    """Use measured per-response accounting, including compaction calls.

    event_msg/token_count snapshots omit compaction usage. Never sum them with
    response records or silently present that fallback as a complete count.
    """
    session_id, model, requests, conflicts, abstained = None, None, {}, set(), 0
    legacy = False
    for entry in _rows(path):
        if entry is None:
            abstained += 1
            continue
        kind, payload = entry.get("type"), entry.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        if kind == "session_meta":
            sid = payload.get("id")
            if session_id and sid and session_id != sid:
                raise ValueError("mixed session identities in one transcript")
            session_id = sid or session_id
        elif kind == "turn_context":
            model = payload.get("model")
        elif kind == "event_msg" and payload.get("type") == "token_count":
            legacy = True
        elif kind == "token_usage_record":
            sid = payload.get("session_id") or payload.get("thread_id")
            if session_id and sid and session_id != sid:
                raise ValueError("mixed session identities in one transcript")
            session_id = sid or session_id
            key, stamp = payload.get("response_id"), entry.get("timestamp")
            parsed = _counts(payload.get("usage"), "codex")
            if not key or not stamp or not parsed:
                abstained += 1
                continue
            _put(requests, conflicts, {"id": key, "tokens": parsed[1], "at": stamp,
                 "counts": parsed[0], "model": model,
                 "model_source": "turn_context.model (declared)", "harness": "codex",
                 "detail": {"reasoning_output": payload["usage"].get("reasoning_output_tokens"),
                            **({'pricing': pricing_metadata(payload['usage'])} if pricing_metadata(payload['usage']) else {})}})
    if not session_id:
        return None
    return session_id, sorted(requests.values(), key=lambda r: (r["at"], r["id"])), abstained + len(conflicts) + int(legacy and not requests)


READERS = {"claude": (read_claude, "anthropic"), "codex": (read_codex, "openai")}


def esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def emit(session_id: str, principal: str, provider: str, fmt: str,
         records: list[dict], out: list[str]) -> int:
    session = f"{BASE}session/{quote(session_id, safe='')}"
    out.append(f"<{session}>")
    out.append(f"    a <{ONTOLOGY}Session> ;")
    out.append(f'    rdfs:label "{esc(fmt)} session {esc(session_id)}" ;')
    out.append(f'    <{ONTOLOGY}sourceKind> "observed" ;')
    out.append(f"    <{ONTOLOGY}actor> <{BASE}principal/{quote(principal, safe='')}> .")
    out.append("")
    written = 0
    for record in records:
        iri = f"{session}/usage/{quote(record['id'], safe='')}"
        out.append(f"<{iri}>")
        out.append(f"    a <{ONTOLOGY}UsageRecord> ;")
        out.append(
            f'    rdfs:label "{esc(record["id"])}: {record["tokens"]} tokens" ;'
        )
        out.append(f'    <{ONTOLOGY}sourceKind> "observed" ;')
        out.append(f"    <{ONTOLOGY}inSession> <{session}> ;")
        out.append(f'    <{ONTOLOGY}provider> "{esc(provider)}" ;')
        out.append(f'    <{ONTOLOGY}tokensConsumed> {record["tokens"]} ;')
        out.append(f'    <{ONTOLOGY}observedAt> "{esc(record["at"])}" .')
        out.append("")
        written += 1
    return written


def session_files(roots: list[Path]) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        if root.is_file():
            files.append(root)
        elif root.is_dir():
            files.extend(sorted(p for p in root.rglob("*.jsonl") if p.is_file()))
        else:
            print(f"  ! {root}: no such file or directory — skipped", file=sys.stderr)
    return files


def ingest(paths: list[Path], principal: str, provider: str | None,
           out: list[str]) -> dict[str, int]:
    stats = {"files": 0, "sessions": 0, "empty": 0,
             "records": 0, "tokens": 0, "abstained": 0, "unrecognised": 0}
    for path in paths:
        stats["files"] += 1
        for fmt, (reader, default_provider) in READERS.items():
            parsed = reader(path)
            if parsed is None:
                continue
            session_id, records, abstained = parsed
            stats["sessions"] += 1
            stats["abstained"] += abstained
            written = emit(session_id, principal, provider or default_provider,
                           fmt, records, out)
            stats["records"] += written
            stats["tokens"] += sum(r["tokens"] for r in records)
            if not written:
                # Q19's population. Emitted, and counted, precisely because a
                # session with no accounting must read UNKNOWN, not zero.
                stats["empty"] += 1
            break
        else:
            stats["unrecognised"] += 1
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("roots", nargs="+", type=Path,
                    help="session log files, or directories searched for *.jsonl")
    ap.add_argument("--principal", required=True,
                    help="the crew principal whose harness wrote these logs. "
                         "REQUIRED because the harness record does not name it: "
                         "it records a session, not who the crew calls the agent "
                         "that ran it. A wrong value attributes real consumption "
                         "to the wrong principal, which Q17 and Q18 then report "
                         "with a straight face.")
    ap.add_argument("--provider",
                    help="override the provider recorded on each UsageRecord. "
                         "Defaults to the harness's own vendor. Set it when the "
                         "harness was proxied elsewhere — the on-disk record does "
                         "not distinguish that, and consumption billed by one "
                         "provider must not be reported under another's name.")
    args = ap.parse_args()

    out = ["@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .", ""]
    stats = ingest(session_files(args.roots), args.principal, args.provider, out)
    print("\n".join(out))

    # THE DENOMINATOR, on stderr, always — the same rule ingest_git_provenance
    # follows. A cost ingest that reported only what it counted would let a run
    # covering a tenth of the logs look like one covering all of them, and a
    # thin total is indistinguishable from a cheap week.
    print(f"\n{stats['sessions']} session(s) from {stats['files']} file(s): "
          f"{stats['records']} usage record(s), {stats['tokens']} token(s) "
          f"attributed to {args.principal}", file=sys.stderr)
    if stats["empty"]:
        print(f"NOTE: {stats['empty']} session(s) carried NO usage record and were "
              f"emitted anyway. That is Q19's population: they must read UNKNOWN, "
              f"never zero.", file=sys.stderr)
    if stats["abstained"]:
        print(f"NOTE: {stats['abstained']} usage entr(ies) were skipped — no "
              f"requestId to deduplicate by, or an incomplete count. The total "
              f"above is a FLOOR, not a measurement.", file=sys.stderr)
    if stats["unrecognised"]:
        print(f"NOTE: {stats['unrecognised']} file(s) matched no verified reader in "
              f"{sorted(READERS)}. Their consumption is ABSENT, not zero.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
