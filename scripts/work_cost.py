"""Request accounting joined to st's explicit focus intervals, without writes."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
from pathlib import Path

from ingest_session_usage import KINDS, READERS

# Binding bases (aegis-59jx step 2a). DECLARED is a paired task declaration.
# INFERRED_HELD is the item the agent held across the request, inferred by the
# caller from its own state; it never outranks a declared focus and never counts
# as observed coverage.
DECLARED, INFERRED_HELD = "declared", "inferred-held"
BASES = (DECLARED, INFERRED_HELD)


def epoch(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value):
            raise ValueError("non-finite timestamp")
        return float(value)
    if not isinstance(value, str):
        raise ValueError("timestamp must be a number or timezone-qualified string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp needs timezone")
    return parsed.timestamp()


def retrieve(params):
    """One result drives displays, close receipts and metric publication.

    The inputs carry paths and task bindings, never a second source of counts.
    Coverage describes the supplied sources only, not lifetime bead accounting.
    """
    bindings = params.get("bindings", [])
    sources = params.get("sources", [])
    if not isinstance(bindings, list) or not isinstance(sources, list):
        raise ValueError("bindings and sources must be arrays")
    intervals = []
    for b in bindings:
        if b.get("schema_version") != 1 or not all(b.get(k) for k in ("rig", "bead", "session", "agent")):
            raise ValueError("binding requires schema_version=1 and rig/bead/session/agent")
        if b.get("basis", DECLARED) not in BASES:
            raise ValueError("binding basis must be one of " + ", ".join(BASES))
        start, end = epoch(b["start"]), epoch(b["end"]) if b.get("end") is not None else None
        if end is not None and end < start:
            raise ValueError("focus end precedes start")
        intervals.append({**b, "start": start, "end": end, "basis": b.get("basis", DECLARED)})
    records, errors, seen_sources = {}, [], set()
    for source in sources:
        fmt = source.get("harness")
        if fmt not in READERS:
            errors.append("unrecognised harness")
            continue
        try:
            path = Path(source["path"])
            source_key = (str(path.resolve()), fmt, source["agent"])
            if source_key in seen_sources:
                continue
            seen_sources.add(source_key)
            parsed = READERS[fmt][0](path)
            if parsed is None:
                errors.append("unrecognised source")
                continue
            session, requests, abstained = parsed
            if abstained:
                errors.append(f"{session}: {abstained} uncountable or conflicting records")
            if not requests:
                errors.append(f"{session}: no measured requests")
            for request in requests:
                key = (fmt, session, request["id"])
                record = {**request, "session": session, "agent": source["agent"]}
                previous = records.get(key)
                if key in records and previous != record:
                    records[key] = None
                    errors.append(f"{session}: conflicting replicated request")
                elif key not in records:
                    records[key] = record
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append(f"source unavailable: {type(exc).__name__}")
    groups, attributed = {}, []
    for record in records.values():
        if record is None:
            continue
        try:
            stamp = epoch(record["at"])
        except (ValueError, TypeError):
            errors.append("invalid request timestamp")
            continue
        matches = [b for b in intervals if b["session"] == record["session"]
                   and b["agent"] == record["agent"]
                   and b.get("harness", record["harness"]) == record["harness"]
                   and b["start"] <= stamp
                   and (b["end"] is None or stamp < b["end"])]
        # A declared focus always wins: an inferred binding is consulted only
        # when no declared binding covers the request (aegis-59jx step 2a).
        declared = [b for b in matches if b["basis"] == DECLARED]
        chosen = declared or matches
        # Identical replayed bindings do not introduce ambiguity.
        unique = {(b["rig"], b["bead"], b["start"], b["end"]) for b in chosen}
        binding = chosen[0] if len(unique) == 1 else None
        inferred = binding is not None and binding["basis"] == INFERRED_HELD
        reason = ("inferred held item" if inferred else "attributed" if binding
                  else "overlapping focus" if chosen else "no proven focus")
        rig, bead = (binding["rig"], binding["bead"]) if binding else ("unknown", "unattributed")
        key = (rig, bead, record["agent"], record["harness"], record["model"], inferred)
        group = groups.setdefault(key, {"rig": rig, "bead": bead, "agent": record["agent"],
             "harness": record["harness"], "model": record["model"],
             "counts": dict.fromkeys(KINDS, 0), "tokens": 0, "requests": 0, "sessions": set(),
             "coverage": "observed"})
        if inferred and "basis" not in group:
            # Only inferred groups carry these keys, so declared and unattributed
            # groups (and every receipt digest built from them) keep their shape.
            group.update(basis=INFERRED_HELD, coverage="inferred")
        for kind, value in record["counts"].items():
            if value is None:
                group["counts"][kind] = None
            elif group["counts"][kind] is not None:
                group["counts"][kind] += value
        group["tokens"] += record["tokens"]
        group["requests"] += 1
        group["sessions"].add(record["session"])
        if record["model"] is None or None in record["counts"].values() or not binding:
            group["coverage"] = "UNKNOWN"
        attributed.append({**record, "rig": rig, "bead": bead, "attribution": reason})
    result = []
    for group in groups.values():
        group["sessions"] = sorted(group["sessions"])
        if errors:
            group["coverage"] = "UNKNOWN"
        result.append(group)
    result.sort(key=lambda x: (x["rig"], x["bead"], x["agent"], x["harness"], x["model"] or ""))
    payload = {"schema_version": 1, "scope": "supplied transcript sources and explicit focus intervals; not lifetime cost",
               "coverage": "UNKNOWN" if errors or not result or any(g["coverage"] != "observed" for g in result) else "observed",
               "groups": result, "errors": errors, "records": attributed,
               "binding_bases": list(BASES)}
    attributed.sort(key=lambda r: (r["harness"], r["session"], r["at"], r["id"]))
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    payload["receipt"] = hashlib.sha256(canonical.encode()).hexdigest()
    return payload
