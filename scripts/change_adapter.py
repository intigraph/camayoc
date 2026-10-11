"""Incremental adapter protocol: bounded changed subjects, not population scans.

The consumer persists /changes records before acknowledgement. Deleted edges carry
old_value, so their former owners are still invalidated. No graph writes here.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import time

import blocked_by as bb
import planes
import seed_work_items as native

BLOCKER = {"blockerKind", "prRef", "tool", "minVersion", "repoRef", "resolvesOn", "resolutionQuery"}
OBS = {"observedAt", "observedStatus", "observedValue", "observedBlockedOn"}
WORK = {"blockedOn", "observes", "closedAt"}
REVIEW = {"reviewAfter", "maxAge", "idleLimit", "ownedBy", "verifies", "verifiedAt", "workKind"}
# A tracker Observation is the ACTIVITY an idleLimit is anchored on (aegis-qx96wr):
# its arrival routes the WorkItem that observes it, which pushes the deadline on.
REVIEW_ACTIVITY = {"observedAt", "observedStatus"}
# A Directive's traceability (aegis-q9m5mp.47). Its edges live in ROOT, the
# declared plane, or the inferred plane (a proposal awaiting promotion), so the
# subscription covers all three; a new Directive arrives as its rdf:type.
DIRECTIVE = {"governedBy", "trackedBy", "inferredAt"}
DIRECTIVE_KINDS = ("directive", "directive-lapse")


def description(kind, *, native_reader=None):
    if kind in DIRECTIVE_KINDS:
        import directive_edges as de
        return {"version": 1, "attributes": sorted(bb.A + p for p in DIRECTIVE),
                "graphs": ["ROOT", de.DECLARED, de.INFERRED],
                "types": [bb.A + "Directive"]}
    props = WORK | OBS | BLOCKER if kind == "blocked" else REVIEW | REVIEW_ACTIVITY
    seed_props = {bb.SEED_STATUS, bb.SCHEMA + "dateModified", bb.SCHEMA + "agent", bb.SCHEMA + "version"}
    return {"version": 1, "attributes": sorted({iri for p in props for iri in bb.term_iris(p)} | seed_props),
            "graphs": [g or "ROOT" for g in bb.graphs()],
            "types": bb.term_iris("WorkItem") + bb.term_iris("Blocker") + [bb.SCHEMA + "Action"] if kind == "blocked" else []}


def local(iri):
    """Only instance IRIs in the configured namespace are adapter keys."""
    if isinstance(iri, str) and iri.startswith(bb.SEED_ITEM):
        return bb.item_iri(iri) if "/" not in iri[len(bb.SEED_ITEM):] and iri != bb.SEED_ITEM else None
    if not isinstance(iri, str) or not iri.startswith(bb.A):
        return None
    name = iri[len(bb.A):]
    if not name or any(c in name for c in '<>"{}\\\n\r\t '):
        raise ValueError("unsafe entity IRI")
    return name


def values(post, subject, prop, reverse=False):
    p = bb.pattern("?v", prop, f"<{bb.item_iri(subject)}>") if reverse else bb.pattern(f"<{bb.item_iri(subject)}>", prop, "?v")
    return [bb._local(str(r["v"])) for r in bb.select(post, f"SELECT ?v WHERE {{ {p} }}")]


def targets(post, kind, changes, items, *, native_reader=None):
    def review_key(entity, graph=None):
        nonlocal native_reader
        if kind != "review" or not isinstance(entity,str) or not entity.startswith(native.ITEM):
            return local(entity)
        if native_reader is None:
            native_reader=native.deployed_reader(post)
        if graph is not None and graph != native_reader.board.graph:
            return None
        from urllib.parse import unquote
        item_id=unquote(entity[len(native.ITEM):])
        if not item_id.startswith(native_reader.board.prefix+'-'):
            return None
        if native_reader.board.subject(item_id)!=entity:
            raise native.NativeUnknown('native change candidate identity ambiguous')
        return item_id  # Existing exact-ID WorkItem's policy, never A + ActionIRI.
    out = {key for value in items for key in [review_key(value) if isinstance(value,str) and value.startswith(native.ITEM) else value] if key}

    if kind in DIRECTIVE_KINDS:
        # Every subscribed change is ON the Directive itself.
        return sorted(out | {e for e in (local(c["entity"]) for c in changes) if e})
    subjects = set()
    for change in changes:
        entity = review_key(change["entity"],change.get("graph"))
        if entity is None:
            continue
        prop = change.get("attribute", "").rsplit("#", 1)[-1].rsplit("/", 1)[-1]
        if kind == "blocked":
            if prop in OBS:
                subjects.update(values(post, entity, "observes", reverse=True))
            else:
                subjects.add(entity)
        else:
            if prop in REVIEW_ACTIVITY:
                out.update(values(post, entity, "observes", reverse=True))
            elif prop in {"verifies", "verifiedAt"}:
                out.update(values(post, entity, "verifies"))
                if prop == "verifies":
                    for field in ("value", "old_value"):
                        value = change.get(field)
                        name = local(value.get("ref")) if isinstance(value, dict) else None
                        if name:
                            out.add(name)
            else:
                out.add(entity)
    if kind == "blocked":
        out.update(subjects)
        for subject in sorted(subjects):
            out.update(values(post, subject, "blockedOn", reverse=True))
            for obs in values(post, subject, "observedBlockedOn", reverse=True):
                out.update(values(post, obs, "observes", reverse=True))
    return sorted(out)


def next_check(post, kind, record, now):
    """Wall-clock dependencies are persisted as deadlines by the consumer.

    External tools and arbitrary ASK expressions cannot supply a finite graph
    dependency set. Only those items retain a bounded 15-minute check.
    """
    deadlines = []
    if kind == "directive-lapse":
        from review_due import parse_instant
        when = parse_instant(record["due_at"]) if record.get("due_at") else None
        return when.timestamp() if when and when.timestamp() > now else None
    if kind == "directive":
        return None
    if kind == "review":
        from review_due import parse_instant
        if record["verdict"] != "DUE":
            for basis in record["basis"]:
                when = parse_instant(basis["due_at"]) if basis["due_at"] else None
                if when and when.timestamp() > now:
                    deadlines.append(when.timestamp())
    else:
        for blocker in record["blockers"]:
            target = blocker["target"]
            if values(post, target, "resolutionQuery") or values(post, target, "blockerKind"):
                deadlines.append(now + 900)
            for raw in values(post, target, "resolvesOn"):
                try:
                    day = dt.date.fromisoformat(raw.strip('"')[:10])
                    when = dt.datetime.combine(day, dt.time(), dt.timezone.utc).timestamp()
                    if when > now:
                        deadlines.append(when)
                except ValueError:
                    pass
    return min(deadlines) if deadlines else None


def evaluate(kind, post, request):
    import review_due as rd
    transport, cache = post, {}
    def post(endpoint, body):
        key = (endpoint, json.dumps(body, sort_keys=True))
        if key not in cache:
            cache[key] = transport(endpoint, body)
        return cache[key]
    now = float(request["now"])
    instant = dt.datetime.fromtimestamp(now, dt.timezone.utc)
    if kind in DIRECTIVE_KINDS:
        return _directive(kind, post, request, instant, now)
    if request.get("discover"):
        if kind == "review":
            names = {bb._local(r["s"]) for prop in ("reviewAfter", "maxAge", "idleLimit")
                     for r in bb.select(post, f"SELECT ?s WHERE {{ {bb.pattern('?s', prop, '?v')} }}")}
        else:
            names = {bb._local(r["w"]) for r in bb.select(post,
                     f"SELECT ?w WHERE {{ {bb.pattern('?w', 'blockedOn', '?d')} }}")}
            observations = {bb._local(r["o"]) for r in bb.select(post,
                            f"SELECT ?o WHERE {{ {bb.pattern('?o', 'observedBlockedOn', '?d')} }}")}
            # Reading every observes edge exceeds the server's row budget.
            # Resolve only blocker-bearing observations, in bounded UNIONs of
            # single reverse patterns; no joins or OFFSET scans of the graph.
            observations = sorted(observations)
            for start in range(0, len(observations), 32):
                batch = observations[start:start + 32]
                columns = " ".join(f"?w{i}" for i in range(len(batch)))
                branches = " UNION ".join(f"{{ ?w{i} <{bb.A}observes> <{bb.A}{obs}> }}"
                                          for i, obs in enumerate(batch))
                for row in bb.select(post, f"SELECT {columns} WHERE {{ {branches} }}"):
                    names.update(bb._local(v) for v in row.values() if v is not None)
        return {"version": 1, "items": sorted(names)}
    if "route" in request:
        return {"version": 1, "items": targets(post, kind, request["route"], [])}
    full = request.get("full", False)
    key = "item" if kind == "blocked" else "entity"
    scope = None if full else targets(post, kind, request.get("changes", []), request.get("items", []))
    if full:
        records = bb.evaluate(post, today=instant.date()) if kind == "blocked" else rd.evaluate(post, now=instant)
    elif kind == "blocked":
        records = [r for item in scope for r in bb.evaluate(post, item=item, today=instant.date(), batch_history=True)]
    else:
        records = [rd.judge(post, item, instant) for item in scope]
        records = [r for r in records if r["basis"]]
    return {"version": 1, "scope": scope, "records": records,
            "next_checks": {r[key]: next_check(post, kind, r, now) for r in records}}


def _directive(kind, post, request, instant, now):
    """The two views of directive_edges.judge, in the emitter's record shape:
    `directive` (verdict UNTRACED drives the proposer) and `directive-lapse`
    (LAPSED drives the alert, with the grace deadline as a next check)."""
    import directive_edges as de
    if request.get("discover"):
        return {"version": 1, "items": de.discover(post)}
    if "route" in request:
        return {"version": 1, "items": targets(post, kind, request["route"], [])}
    full = request.get("full", False)
    scope = de.discover(post) if full else targets(
        post, kind, request.get("changes", []), request.get("items", []))
    records = []
    for name in scope:
        # Partial scope covers every requested key, including a retired one.
        # No record for that key tells the consumer to remove its old verdict.
        # Filtering the scope itself makes the entire mixed batch UNKNOWN.
        if not de.is_directive(post, name):
            continue
        r = de.judge(post, name, instant)
        if kind == "directive-lapse":
            r = {**r, "verdict": r["lapse"], "event_id": r["lapse_event_id"]}
        records.append(r)
    return {"version": 1, "scope": None if full else scope, "records": records,
            "next_checks": {r["entity"]: next_check(post, kind, r, now) for r in records}}


def main(kind, describe=False):
    if describe:
        print(json.dumps(description(kind)))
    else:
        request = json.load(sys.stdin)
        last_read = 0.0
        def post(endpoint, body):
            nonlocal last_read
            # At most eight reads/second, including catalogue reconciliation.
            time.sleep(max(0, last_read + 0.125 - time.monotonic()))
            last_read = time.monotonic()
            return planes._post(endpoint, body, client=bb.CLIENT)
        print(json.dumps(evaluate(kind, post, request)))
    return 0
