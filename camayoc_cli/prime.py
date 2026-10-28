"""`cy prime`: the stack prime contract (tambo docs/stack/PRIME-CONTRACT.md, stack-prime/1).

Static primer from primer.md (no network, no store, no config), then a live
summary bounded to under 1 s, read-only (no file or directory is created) and
offline-safe (an unreachable Quipu still prints the full primer and exits 0).
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

from . import __version__, definitions, memory, planes, quipu

PRIMER = Path(__file__).resolve().parent / "primer.md"
DEADLINE_S = 0.8
JOB = "agent-memory tools over Quipu: record and recall typed entities"


def primer_text() -> str:
    return PRIMER.read_text(encoding="utf-8")


def sections(text: str) -> list[tuple[str, str]]:
    """[(heading, body including the heading line)] for every `## ` section."""
    parts = re.split(r"(?m)^(?=## )", text)
    out = []
    for part in parts[1:]:
        heading = part.splitlines()[0][3:].strip()
        out.append((heading, part.rstrip() + "\n"))
    return out


def live(deadline_s: float = DEADLINE_S) -> dict:
    start = time.monotonic()
    server = quipu.server()
    summary = {"server": server, "credential": "present" if quipu.has_token() else "absent",
               "queued": memory.queue_length()}

    def remaining() -> float:
        return max(0.05, deadline_s - (time.monotonic() - start))

    def done(status: str, **extra) -> dict:
        summary.update(extra)
        return {"status": status, "elapsed_ms": int((time.monotonic() - start) * 1000), "summary": summary}

    client = quipu.Client(server)
    try:
        client.health(timeout=remaining())
    except quipu.TimedOut:
        return done("partial", note=f"{server} did not answer /health inside {int(deadline_s * 1000)} ms")
    except quipu.Unreachable:
        return done("unreachable", fix=f"start quipu-server or set QUIPU_SERVER (tried {server})")
    except quipu.QuipuError as exc:
        return done("unreadable", note=str(exc)[:200])
    a = definitions.load()["namespace"]
    froms = " ".join(f"FROM <{g}>" for g in planes.iris().values())
    sparql = (f"SELECT ?kind (COUNT(DISTINCT ?r) AS ?n) {froms} WHERE {{ ?r <{a}naturalKey> ?k ; a ?kind . "
              f"FILTER NOT EXISTS {{ ?r <{a}supersededBy> ?newer }} }} GROUP BY ?kind ORDER BY ?kind LIMIT 20")
    try:
        rows = client.query(sparql, timeout=remaining())
    except quipu.TimedOut:
        return done("partial", note="record counts cut short by the 1 s budget")
    except quipu.QuipuError as exc:
        return done("unreadable", note=str(exc)[:200])
    counts = {memory.local_name(quipu.value(r.get("kind"))).lower(): int(quipu.value(r.get("n")) or 0)
              for r in rows}
    return done("ok", current_records=counts)


def live_lines(state: dict) -> list[str]:
    s = state["summary"]
    lines = [f"- quipu: {s['server']} ({state['status']}, {state['elapsed_ms']} ms)",
             f"- credential: {s['credential']} (never printed)"]
    if "current_records" in s:
        counts = s["current_records"]
        shown = ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "none yet"
        lines.append(f"- current memory records: {shown}")
    for key in ("fix", "note"):
        if key in s:
            lines.append(f"- {key}: {s[key]}")
    lines.append(f"- offline queue: {s['queued']} record(s)" + (" (run `cy flush`)" if s["queued"] else ""))
    return lines


def run(*, brief: bool = False, as_json: bool = False, static: bool = False, section: str | None = None) -> int:
    text = primer_text()
    secs = sections(text)
    if section is not None:
        match = [body for heading, body in secs if heading.lower().startswith(section.lower())]
        if not match:
            print(f"unknown section '{section}'; sections: {', '.join(h for h, _ in secs)}", file=sys.stderr)
            return 2
        print(match[0], end="")
        return 0
    state = ({"status": "skipped", "elapsed_ms": 0, "summary": {}} if static else live())
    status = "ok" if state["status"] in ("ok", "skipped") else "degraded"
    if as_json:
        doc = {"schema": "stack-prime/1", "tool": "camayoc", "version": __version__,
               "primer": {"sections": [h for h, _ in secs]}, "live": state, "status": status}
        if not brief:
            doc["primer"]["text"] = text
        print(json.dumps(doc, indent=2, sort_keys=True))
        return 0
    if brief:
        out = [f"camayoc {__version__}: {JOB}"]
        if static:
            what = next(body for heading, body in secs if heading.lower().startswith("what"))
            out += [ln for ln in what.splitlines()[1:] if ln.strip()][:6]
        else:
            out += live_lines(state)
        out = out[:9]
        while len(out) < 4:
            out.append(f"- entities: {', '.join(definitions.entity_names())}")
        out.append("Run `cy prime` for the full primer.")
        print("\n".join(out))
        return 0
    print(text, end="" if text.endswith("\n") else "\n")
    if not static:
        print("\n## Live state\n")
        print("\n".join(live_lines(state)))
    return 0
