"""Incremental adapter protocol: bounded changed subjects, not population scans.

The consumer persists /changes records before acknowledgement. Deleted edges carry
old_value, so their former owners are still invalidated. No graph writes here.

Request budget (aegis-ssvr C1). A `--changes` request may carry
`"budget": {"max_requests": k}`, k an integer >= 1. Every Quipu request this
process sends is counted at the single HTTP choke point (`planes._post`), and
request k+1 is refused BEFORE it is sent: the process exits nonzero (3) with no
stdout, never exceeding k. Every successful response carries
`"cost": {"quipu_requests": n}`, n <= k; without a budget the call is uncapped
and still reports its cost.

Paged, snapshot-pinned discovery. `{"discover": true, "now": t, "as_of_tx": W,
"cursor": null | "<opaque>", "budget": {...}}` returns `{"version": 1,
"items": [...], "next_cursor": "<opaque>"}` or `{..., "complete": true}`.
Every discovery /query carries `"tx": W`, so every page reads the same
snapshot. Pages are keyset pages over the full candidate IRI (`ORDER BY
STR(?s)`, `FILTER(STR(?s) > after)`), merged across graphs and sources, so
across all pages of one snapshot each item appears exactly once. A page stops
early, at the last fully processed key, when the request budget is spent; only
a call that cannot make any progress within its budget fails. A legacy
`{"discover": true, "now": t}` (no as_of_tx) is answered exactly as before:
one response, no cursor.

Reconcile contract. For every item k whose evaluation against graph state at
any T >= W would be actionable or would transition a tracked verdict, k is in
discover(as_of T) or is already tracked by the consumer. route() may
over-approximate. Per kind (each discover candidate set is at least the set
the full evaluation judges):

* blocked: the evaluation judges WorkItems with a blockedOn edge, or whose
  LATEST Observation carries observedBlockedOn. Paged discovery returns
  exactly those (phase 0: blockedOn subjects; phase 1: an owner of a
  blocker-bearing Observation when that Observation is its latest and it has
  no blockedOn of its own). An item that loses its last blocker is no longer
  discoverable and produces no record: it is either tracked (its verdict is
  resolved through the tracked set) or never had an actionable verdict.
* review: the evaluation judges entities with reviewAfter/maxAge/idleLimit in
  the ordinary graphs, or a keyed inferred memory whose memory_basis is not
  empty. Paged discovery returns exactly that union.
* directive / directive-lapse: the evaluation judges every directly typed
  Directive; discovery returns every directly typed Directive.
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import re
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
#: What --describe declares this adapter honours: the hard request cap with an
#: exact cost report, and the reconcile contract of paged discovery (above).
CONTRACTS = {"budget_cap": 1, "reconcile_contract": 1}


def description(kind, *, native_reader=None):
    if kind in DIRECTIVE_KINDS:
        import directive_edges as de
        return {"version": 1, "attributes": sorted(bb.A + p for p in DIRECTIVE),
                "graphs": ["ROOT", de.DECLARED, de.INFERRED],
                "types": [bb.A + "Directive"], **CONTRACTS}
    props = WORK | OBS | BLOCKER if kind == "blocked" else REVIEW | REVIEW_ACTIVITY
    extra_attributes = set()
    graphs = bb.graphs()
    if kind == "review":
        import review_due as rd
        graphs = list(dict.fromkeys([*graphs, planes.plane_for("inferred")]))
        extra_attributes = {rd.TYPE} | {iri for iris in rd.MEMORY_PROPERTIES.values() for iri in iris}
    seed_props = {bb.SEED_STATUS, bb.SCHEMA + "dateModified", bb.SCHEMA + "agent", bb.SCHEMA + "version"}
    return {"version": 1, "attributes": sorted({iri for p in props for iri in bb.term_iris(p)} | seed_props | extra_attributes),
            "graphs": [g or "ROOT" for g in graphs],
            "types": bb.term_iris("WorkItem") + bb.term_iris("Blocker") + [bb.SCHEMA + "Action"] if kind == "blocked" else [],
            **CONTRACTS}


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
        if kind != "review" or not isinstance(entity, str):
            return local(entity)
        if not entity.startswith(native.ITEM):
            # Review ages apply to any entity, not just legacy WorkItems.
            # Keep legacy keys stable and preserve other stored web IRIs.
            if entity.startswith(("https://", "http://")) and not entity.startswith(bb.A):
                return bb.item_iri(entity)
            return local(entity)
        if native_reader is None:
            native_reader=native.deployed_reader(post)
        if graph is not None and graph != native_reader.board.graph:
            return None
        from urllib.parse import unquote
        suffix=entity[len(native.ITEM):]
        comment=re.fullmatch(r'([^/]+)/comment/([0-9]+)',suffix)
        item_id=unquote(comment.group(1) if comment else suffix)
        if not item_id.startswith(native_reader.board.prefix+'-'):
            return None
        # Comments belong to the exact parent WorkItem's review policy. Never
        # turn the comment path into an item ID or silently skip the whole batch.
        expected=native_reader.board.subject(item_id)
        if comment:expected+='/comment/'+comment.group(2)
        if expected!=entity:
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
                        name = review_key(value.get("ref")) if isinstance(value, dict) else None
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
    if request.get("discover") and "as_of_tx" in request:
        return discover_paged(kind, post, request)
    if kind in DIRECTIVE_KINDS:
        return _directive(kind, post, request, instant, now)
    if request.get("discover"):
        if kind == "review":
            names = set(rd.discover(post))
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
        elif r["verdict"] == de.PROPOSED:
            # chaski's directive-untraced emitter admits only TRACED/UNTRACED/
            # UNKNOWN. One PROPOSED record made every incremental run UNKNOWN
            # (3212 runs, none ok; aegis-df7h). A proposal awaiting promotion is
            # still untraced in ROOT, and the proposer treats it as a noop.
            # Same event identity judge() gives an UNTRACED record: chaski refuses
            # an UNTRACED verdict without an event_id (emitter ProtocolError).
            r = {**r, "verdict": de.UNTRACED, "event_id": de.event_id(name)}
        records.append(r)
    return {"version": 1, "scope": None if full else scope, "records": records,
            "next_checks": {r["entity"]: next_check(post, kind, r, now) for r in records}}


def budget_of(request):
    """The request cap, or None. A malformed budget is refused, never ignored:
    a field this adapter does not understand may be a limit it would break."""
    if "budget" not in request:
        return None
    budget = request["budget"]
    if not isinstance(budget, dict) or set(budget) != {"max_requests"}:
        raise ValueError('budget must be {"max_requests": k}')
    k = budget["max_requests"]
    if type(k) is not int or k < 1:
        raise ValueError("budget.max_requests must be an integer >= 1")
    return k


def run(kind, request, post):
    """One --changes call: arm the cap, evaluate, attach the exact cost."""
    planes.start_request_budget(budget_of(request))
    result = evaluate(kind, post, request)
    return {**result, "cost": {"quipu_requests": planes.requests_sent()}}


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
        try:
            result = run(kind, request, post)
        except planes.RequestBudgetExceeded as exc:
            print(f"camayoc adapter: request budget exhausted: {exc}", file=sys.stderr)
            return 3
        print(json.dumps(result))
    return 0


# ---- paged, snapshot-pinned discovery (aegis-ssvr C1) ----------------------
#: Keys per graph per keyset query. Small enough to stay far below the
#: server's row budget, large enough that an idle population is a few reads.
PAGE = 200
#: Blocker-bearing Observations per page, and per committed unit within it:
#: each unit resolves owners and their latest Observation in batched reads.
OBS_PAGE, OBS_UNIT = 64, 8
_UNSAFE_KEY = re.compile(r'[<>"{}\\\s]')


def _key(term):
    """A row's subject as the full IRI the server ordered and filtered on."""
    iri = bb._iri(str(term))
    if _UNSAFE_KEY.search(iri) or not ("://" in iri or iri.startswith("urn:")):
        raise ValueError(f"discovery key is not a full IRI: {iri[:80]!r}")
    return iri


def _keyset_query(branches, after, limit):
    union = " UNION ".join("{ " + b + " }" for b in branches)
    keyset = f' FILTER(STR(?s) > "{after}")' if after is not None else ""
    return f"SELECT DISTINCT ?s WHERE {{ {union}{keyset} }} ORDER BY STR(?s) LIMIT {limit}"


def _page(post, sources, after, limit):
    """Keys strictly after `after`, in STR order, across every (source, graph).

    Each graph returns its own first `limit` keys. Where a graph returned a
    full page, keys beyond its last one are unseen, so the page is cut at the
    smallest such last key: every key at or below the cut has been seen from
    every graph. Returns ([(key, {source tags})], exhausted)."""
    found, cut = {}, None
    for tag, branches, graphs in sources:
        query = _keyset_query(branches, after, limit)
        for graph in graphs:
            result = post("/query", {"query": query, **({"graph": graph} if graph else {})})
            rows = result.get("rows")
            if not isinstance(rows, list) or result.get("truncated"):
                raise ValueError(f"discovery page unproven in {graph or 'default'}")
            keys = sorted({_key(row["s"]) for row in rows})
            if any(after is not None and k <= after for k in keys):
                raise ValueError("discovery page ignored its keyset bound")
            if keys and len(rows) >= limit:
                cut = keys[-1] if cut is None else min(cut, keys[-1])
            for k in keys:
                found.setdefault(k, set()).add(tag)
    keys = sorted(k for k in found if cut is None or k <= cut)
    return [(k, found[k]) for k in keys], cut is None


def _keyset(post, sources, after, limit, unit, process):
    """Yield (emitted, after) once per committed unit, in key order."""
    while True:
        page, exhausted = _page(post, sources, after, limit)
        for start in range(0, len(page), unit):
            chunk = page[start:start + unit]
            emitted = process(chunk)
            after = chunk[-1][0]
            yield emitted, after
        if exhausted:
            return


def _phases(kind, post):
    """The discovery phases of one kind, each `after -> generator`."""
    if kind in DIRECTIVE_KINDS:
        sources = [("directive", [f"?s a ?t . FILTER(?t = <{bb.A}Directive>)"], [None])]
        return [lambda after: _keyset(post, sources, after, PAGE, PAGE,
                                      lambda chunk: [bb._local(k) for k, _ in chunk])]
    graphs = bb.graphs()
    if kind == "review":
        import review_due as rd
        ages = [f"?s <{iri}> ?v" for prop in ("reviewAfter", "maxAge", "idleLimit")
                for iri in bb.term_iris(prop)]
        memories = [f"?s <{iri}> ?v" for iri in rd.MEMORY_PROPERTIES["naturalKey"]]
        sources = [("age", ages, graphs), ("memory", memories, [planes.plane_for("inferred")])]

        def process(chunk):
            # An ordinary age needs no further read; a memory-only key is a
            # candidate only while its memory review obligation is live.
            return [bb._local(k) for k, tags in chunk
                    if "age" in tags or rd.memory_basis(post, bb._local(k))]
        return [lambda after: _keyset(post, sources, after, PAGE, 1, process)]

    blocked = [("blocked", [f"?s <{iri}> ?d" for iri in bb.term_iris("blockedOn")], graphs)]
    observed = [("observed", [f"?s <{iri}> ?d" for iri in bb.term_iris("observedBlockedOn")], graphs)]

    def owners(chunk):
        names = [bb._local(k) for k, _ in chunk]
        columns = " ".join(f"?w{i}" for i in range(len(chunk)))
        branches = bb.union([f"?w{i} <{bb.A}observes> <{k}>" for i, (k, _) in enumerate(chunk)])
        owned = {}
        for row in bb.select(post, f"SELECT {columns} WHERE {{ {branches} }}"):
            for i, obs in enumerate(names):
                if row.get(f"w{i}") is not None:
                    owned.setdefault(bb._local(row[f"w{i}"]), set()).add(obs)
        candidates = sorted(w for w in owned if not w.startswith(bb.SEED_ITEM))
        if not candidates:
            return []
        # Phase 0 already returned every item with a blockedOn edge.
        columns = " ".join(f"?d{j}" for j in range(len(candidates)))
        branches = bb.union([f"<{bb.item_iri(w)}> <{iri}> ?d{j}" for j, w in enumerate(candidates)
                             for iri in bb.term_iris("blockedOn")])
        rows = bb.select(post, f"SELECT {columns} WHERE {{ {branches} }}")
        declared = {w for j, w in enumerate(candidates) if any(r.get(f"d{j}") is not None for r in rows)}
        remaining = [w for w in candidates if w not in declared]
        latest = bb.latest_observations(post, remaining)
        # Each item has ONE latest Observation, so it is returned exactly once:
        # on the page holding that Observation, if it bears a blocker.
        return sorted(w for w in remaining if latest.get(w) in owned[w])

    return [lambda after: _keyset(post, blocked, after, PAGE, PAGE,
                                  lambda chunk: [bb._local(k) for k, _ in chunk]),
            lambda after: _keyset(post, observed, after, OBS_PAGE, OBS_UNIT, owners)]


def _cursor_out(kind, tx, phase, after):
    raw = json.dumps({"v": 1, "kind": kind, "tx": tx, "phase": phase, "after": after},
                     separators=(",", ":"), sort_keys=True)
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _cursor_in(kind, tx, cursor, phases):
    if cursor is None:
        return 0, None
    if not isinstance(cursor, str):
        raise ValueError("cursor must be a string or null")
    try:
        state = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    except ValueError as exc:
        raise ValueError("unreadable cursor") from exc
    if not isinstance(state, dict) or state.get("v") != 1 or state.get("kind") != kind:
        raise ValueError("cursor belongs to another adapter or version")
    if state.get("tx") != tx:
        raise ValueError("cursor belongs to another snapshot (as_of_tx)")
    phase, after = state.get("phase"), state.get("after")
    if type(phase) is not int or not 0 <= phase < phases:
        raise ValueError("cursor phase out of range")
    if after is not None and not isinstance(after, str):
        raise ValueError("cursor key must be a string")
    return phase, None if after is None else _key(after)


def discover_paged(kind, post, request):
    tx = request["as_of_tx"]
    if type(tx) is not int or tx < 0:
        raise ValueError("as_of_tx must be an integer >= 0")
    def pinned(endpoint, body):
        # Every discovery read is the same snapshot, whatever page it serves.
        return post(endpoint, {**body, "tx": tx})
    phases = _phases(kind, pinned)
    phase, after = _cursor_in(kind, tx, request.get("cursor"), len(phases))
    items, moved = [], False
    try:
        while phase < len(phases):
            for emitted, after in phases[phase](after):
                items.extend(emitted)
                moved = True
            phase, after, moved = phase + 1, None, True
    except planes.RequestBudgetExceeded:
        if not moved:
            raise  # Not one unit fits this budget: fail rather than spin.
        return {"version": 1, "items": items, "next_cursor": _cursor_out(kind, tx, phase, after)}
    return {"version": 1, "items": items, "complete": True}
