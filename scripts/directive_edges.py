#!/usr/bin/env python3
"""Give a new Directive its traceability edges (aegis-q9m5mp.47).

DirectiveTraceabilityShape wants every Directive governedBy a Policy and
trackedBy a WorkItem. The backfill (aegis-4c3ppi) took the board to 867/867,
but Directives are captured every day without those edges, so that number
decays unless something gives each new one its edges.

Two halves, deliberately separate:

* `judge` / `discover` READ ONLY. They are the verdict adapter a change-driven
  chaski emitter runs:
    TRACED    both edges in ROOT or crew/declared (what the conformance
              instrument counts);
    PROPOSED  both edges only in crew/inferred, awaiting promotion;
    UNTRACED  neither.
* `propose` WRITES, and only to crew/inferred. It asks Jev which Policy governs
  the Directive (the backfill's instrument, floor 0.75) and takes the tracker
  from that Policy's existing members. Ingress never promotes its own output:
  a non-author moves the edges to crew/declared with promote_plane.py. Measured
  on the backfill, even picks at >= 0.90 were overturned 5 times in 211
  ([ian-q47-overturn-measured]), so that review is load-bearing.

`propose` is idempotent. A Directive that is no longer UNTRACED is left alone,
so a re-delivered event writes nothing. A pick below the floor, "none of
these", or a policy with no tracker yet writes nothing and says why: that
Directive needs a human ruling, as the backfill's low-confidence rows did.

    python3 scripts/directive_edges.py judge <name>        # one verdict, JSON
    python3 scripts/directive_edges.py adapter --describe  # chaski adapter (also --changes)
    python3 scripts/directive_edges.py lapse-adapter --changes < request.json
    echo '<event>' | python3 scripts/directive_edges.py propose [--dry-run]
    python3 scripts/directive_edges.py repair [<name>...] [--apply]  # aegis-1e5
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import sys

import blocked_by as bb
import planes
from directive_deadline import ROOT_GRAPH

TRACED, PROPOSED, UNTRACED = "TRACED", "PROPOSED", "UNTRACED"
#: The lapse view (sattler's ruling (a), [ian-q47-grace-window-blocker]): the
#: an explicit traceability deadline replaces the legacy GRACE fallback.
#: A still-untraced directive reaches a person when its deadline passes.
LAPSED, NOT_LAPSED = "LAPSED", "NOT_LAPSED"
GRACE = dt.timedelta(days=2)
EDGES = ("governedBy", "trackedBy")
PROV = "http://www.w3.org/ns/prov#"
#: The aegis-4c3ppi v4 floor. Below it a pick is a guess, not a proposal.
FLOOR = 0.75
#: A caller KIND of its own, so these reads and the rare write never land in
#: another producer's budget (a harness that reuses a producer's label pollutes
#: that producer's alert).
CLIENT = "camayoc-directive-edges"
ACTOR = "camayoc-directive-edges"
DECLARED = planes.plane_for("declared")
INFERRED = planes.plane_for("inferred")
XSD_DT = "http://www.w3.org/2001/XMLSchema#dateTime"
INSTRUCTIONS = ("Which policy GOVERNS this directive, i.e. which general rule is it an "
                "instance of? Pick none-of-these only if no listed policy is a fair home for it.")
NONE_TEXT = "none of these policies fits it"


def _rows(post, query: str, graph: str | None = None) -> list[dict]:
    out = post("/query", {"query": query, "verbose": True, **({"graph": graph} if graph else {})})
    if not isinstance(out.get("rows"), list) or out.get("truncated"):
        raise ValueError(f"query unproven in {graph or 'default'}: {query[:80]}")
    return out["rows"]


def _iri(term: str) -> str:
    """Expand Quipu compact IRIs using the shared reader contract."""
    return bb._iri(str(term))


#: An object counts as an edge only when its STORED string is an absolute
#: http(s)/urn IRI. Quipu compacts full IRIs to `a:x` in query rows, so a row's
#: text cannot tell `<http://.../x>` from a malformed `<a:x>` (an IRI whose
#: scheme is "a"); only STR() inside the query can (aegis-1e5).
_ABSOLUTE = ('(STRSTARTS(STR(?o), "http://") || STRSTARTS(STR(?o), "https://") || '
             'STRSTARTS(STR(?o), "urn:"))')
WELL_FORMED = f"FILTER(isIRI(?o) && {_ABSOLUTE})"
MALFORMED = f"FILTER(isIRI(?o) && !{_ABSOLUTE})"


def _has(post, entity: str, prop: str, graphs) -> bool:
    """A malformed object is no edge: such a Directive reads UNTRACED, never a
    PROPOSED that promote_plane can only refuse (aegis-1e5)."""
    q = f"SELECT ?o WHERE {{ <{bb.A}{entity}> <{bb.A}{prop}> ?o . {WELL_FORMED} }}"
    return any(_rows(post, q, g) for g in graphs)


def _typed_link(post, entity: str, prop: str, expected: str) -> bool:
    """A dangling or wrongly typed link cannot hide an overdue directive."""
    graphs = (ROOT_GRAPH, DECLARED)
    targets = {_iri(row["o"]) for graph in graphs for row in _rows(
        post, f"SELECT ?o WHERE {{ <{bb.A}{entity}> <{bb.A}{prop}> ?o . {WELL_FORMED} }}",
        graph)}
    for target in targets:
        if (not target.startswith(("http://", "https://", "urn:")) or
                any(c.isspace() or c in '<>"{}|\\^`' for c in target)):
            raise ValueError("unsafe or unsupported traceability target IRI")
        for graph in graphs:
            types = _rows(post, f"SELECT ?t WHERE {{ <{target}> a ?t . "
                          f"FILTER(?t = <{bb.A}{expected}>) }}", graph)
            if types:
                return True
    return False


def event_id(entity: str) -> str:
    """One untraced episode per Directive: a re-detected one is the same event."""
    return "sha256:" + hashlib.sha256(f"directive-untraced\0{entity}".encode()).hexdigest()


def _instant(raw) -> dt.datetime | None:
    """One instant parser for the stack: review_due's, which also accepts
    nanosecond fractions on Python 3.10 (the chaski host)."""
    from review_due import parse_instant
    raw = raw.get("value") if isinstance(raw, dict) else raw
    return parse_instant(str(raw))


def anchor(post, entity: str, verdict: str) -> dt.datetime | None:
    """Where the grace window starts. PROPOSED: when the edges were inferred.
    UNTRACED: when the Directive was captured (its episode's generatedAtTime).
    None when nothing says: an unanchored age is neither fresh nor lapsed."""
    if verdict == PROPOSED:
        rows = _rows(post, f"SELECT ?t WHERE {{ <{bb.A}{entity}> <{bb.A}inferredAt> ?t }}", INFERRED)
        times = [t for t in (_instant(r["t"]) for r in rows) if t]
        return max(times) if times else None
    episodes = _rows(post, f"SELECT ?e WHERE {{ <{bb.A}{entity}> <{PROV}wasGeneratedBy> ?e }}")
    times = []
    for r in episodes:
        e = _iri(r["e"])
        times += [t for t in (_instant(x["t"]) for x in _rows(
            post, f"SELECT ?t WHERE {{ <{e}> <{PROV}generatedAtTime> ?t }}")) if t]
    return min(times) if times else None


def judge(post, entity: str, now: dt.datetime | None = None, *,
          isolate_deadline: bool = False) -> dict:
    """Both views of one Directive. `verdict` drives the proposer; `lapse`
    drives the alert. A TRACED Directive never lapses.

    A stored deadline that cannot be read as one instant (conflicting values,
    malformed cell, bound reached) raises DeadlineUnknown. With
    `isolate_deadline` (the batch adapter, aegis-b9k) it is a fact about this
    item only: the verdict does not depend on it and is kept, and the lapse
    view treats the deadline like an unanchored one, LAPSED now with the reason
    in evidence, so it reaches a person instead of failing the batch or
    waiting forever. There is no grace fallback either way. A failed read is
    not DeadlineUnknown and always propagates."""
    now = now or dt.datetime.now(dt.timezone.utc)
    traced = all(_typed_link(post, entity, prop, cls) for prop, cls in
                 (("governedBy", "Policy"), ("trackedBy", "WorkItem")))
    proposed = not traced and all(_has(post, entity, p, (INFERRED,)) for p in EDGES)
    verdict = TRACED if traced else PROPOSED if proposed else UNTRACED
    from directive_deadline import DeadlineUnknown, deadline
    unreadable = None
    try:
        explicit_due = None if verdict == TRACED else deadline(
            lambda q, g: _rows(post, q, g), bb.A + entity, DECLARED, _instant)
    except DeadlineUnknown as exc:
        if not isolate_deadline:
            raise
        explicit_due, unreadable = None, str(exc)
    start = (None if verdict == TRACED or explicit_due or unreadable
             else anchor(post, entity, verdict))
    due = explicit_due or (start + GRACE if start else None)
    # UNANCHORED counts as LAPSED (wu, [wu-cm84-review] item 1): measured, 407 of
    # 868 Directives have no datable capture episode. A stateless adapter cannot
    # remember first sight, so an untraced Directive with no age reaches a person
    # NOW rather than never.
    unanchored = verdict != TRACED and due is None
    lapse = LAPSED if unanchored or (due and due <= now) else NOT_LAPSED
    return {"entity": entity, "verdict": verdict,
            # KNOWN LIMIT (item 2): constant per Directive, so a proposal that is
            # withdrawn (back to UNTRACED) does not re-trigger the proposer. The
            # lapse view still fires for it, so it is late, not silent.
            "event_id": event_id(entity) if verdict == UNTRACED else None,
            "lapse": lapse,
            # A lapse is a NEW event each time the anchor moves: a proposal that
            # lapses, is withdrawn and re-proposed is a second lapse, not a replay.
            "lapse_event_id": (event_id(f"{entity}\0{verdict}\0"
                                        f"{(explicit_due or start).isoformat() if (explicit_due or start) else 'unreadable-deadline' if unreadable else 'unanchored'}")
                               if lapse == LAPSED else None),
            "due_at": due.isoformat() if due else None,
            "explicit_deadline": explicit_due is not None,
            "evidence": (f"{verdict}; traceability deadline UNKNOWN: {unreadable}"
                         if unreadable else verdict),
            "owner": None}


def is_directive(post, entity: str) -> bool:
    """Only a DIRECTLY typed Directive is judged. The change feed also routes
    other subjects that gain a subscribed attribute: a new Policy declaring its
    own trackedBy was routed, judged UNTRACED and paged DirectiveUntraced
    (2026-10-08 02:02Z, q9m5mp.47). Bound subject, single pattern."""
    return bool(_rows(post, f"SELECT ?t WHERE {{ <{bb.A}{entity}> a ?t . "
                            f"FILTER(?t = <{bb.A}Directive>) }}"))


def discover(post) -> list[str]:
    """Every DIRECTLY typed Directive: the asserted-only form the conformance
    instrument uses, so a subclass instance is not judged as a Directive."""
    q = f"SELECT ?s WHERE {{ ?s a ?t . FILTER(?t = <{bb.A}Directive>) }}"
    return sorted({bb._local(r["s"]) for r in _rows(post, q)})


def _policy_iris(post) -> set[str]:
    """Directly typed Policies, in ROOT (the original set) and crew/declared
    (every policy minted since through ingress + non-author promotion)."""
    out = set()
    for graph in (None, DECLARED):
        out |= {_iri(r["p"]) for r in _rows(
            post, f"SELECT ?p WHERE {{ ?p a ?t . FILTER(?t = <{bb.A}Policy>) }}", graph)}
    return out


def policies(post) -> dict[str, str]:
    """Policy IRI -> its claim, for DIRECTLY typed Policies only.

    aegis:claim is not Policy-specific: measured 2026-10-08, 615 Artifacts, 8
    SecurityFindings and 2 Observations carry it beside 50 Policies, so reading
    the predicate alone offered Jev 676 options and failed its 255 limit before
    any request was sent. Single patterns, intersected here (a join exceeds
    quipu's budget live)."""
    typed, claims = _policy_iris(post), {}
    for graph in (None, DECLARED):
        claims.update({_iri(r["p"]): str(r["c"]) for r in _rows(
            post, f"SELECT ?p ?c WHERE {{ ?p <{bb.A}claim> ?c }}", graph)})
    return {p: c for p, c in claims.items() if p in typed}


def trackers(post) -> dict[str, str]:
    """Policy IRI -> the WorkItem its traced members are trackedBy, derived
    from the edges themselves (most common wins), never a second copy."""
    pairs: dict[str, dict[str, set]] = {p: collections.defaultdict(set) for p in EDGES}
    for prop in EDGES:
        q = f"SELECT ?s ?o WHERE {{ ?s <{bb.A}{prop}> ?o }}"
        for graph in (None, DECLARED):
            for r in _rows(post, q, graph):
                pairs[prop][r["s"]].add(_iri(r["o"]))
    votes: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for s, governed in pairs["governedBy"].items():
        for policy in governed:
            votes[policy].update(pairs["trackedBy"].get(s, ()))
    derived = {p: c.most_common(1)[0][0] for p, c in votes.items() if c}
    # A Policy that DECLARES its tracker (aegis:trackedBy on the Policy itself)
    # wins: a declared fact beats a vote, and a NEW policy has no members to
    # vote yet (q9m5mp.47, design-prefers-queryable-sources).
    typed = _policy_iris(post)
    declared = {_iri(p): min(ws) for p, ws in pairs["trackedBy"].items()
                if _iri(p) in typed and ws}
    return {**derived, **declared}


def _state(post, entity: str) -> str:
    texts = []
    for prop in ("http://www.w3.org/2000/01/rdf-schema#label",
                 "http://www.w3.org/2000/01/rdf-schema#comment"):
        texts += [str(r["v"]) for r in _rows(post, f"SELECT ?v WHERE {{ <{bb.A}{entity}> <{prop}> ?v }}")]
    return f"DIRECTIVE {entity}\n" + "\n".join(texts)


def jev_limit() -> int:
    import jev
    return jev.MAX_CHOICE_OPTIONS


def _option(iri: str) -> str:
    return iri.rstrip("/#").rsplit("/", 1)[-1].rsplit("#", 1)[-1]


def plan(post, client, entity: str, now: dt.datetime) -> dict:
    """What `propose` would write for one Directive. Reads, plus one Jev call."""
    if not is_directive(post, entity):
        return {"entity": entity, "outcome": "noop", "verdict": "NOT_A_DIRECTIVE"}
    state = judge(post, entity, now)
    verdict = state["verdict"]
    if state["explicit_deadline"] and state["lapse"] == NOT_LAPSED:
        return {"entity": entity, "outcome": "noop", "verdict": "DEFERRED",
                "due_at": state["due_at"]}
    if verdict != UNTRACED:
        return {"entity": entity, "outcome": "noop", "verdict": verdict}
    claims, tracked = policies(post), trackers(post)
    by_option = {_option(p): p for p in claims}
    # One option is reserved for "none of these"; refuse loudly before sending.
    if not 0 < len(by_option) < jev_limit():
        raise ValueError(f"{len(by_option)} candidate policies; Jev takes 1..{jev_limit() - 1} "
                         "plus none-of-these. Nothing was sent.")
    answer = client.choice(_state(post, entity), INSTRUCTIONS,
                           {k: claims[p][:280] for k, p in by_option.items()}, NONE_TEXT)
    choice, confidence = answer.get("choice"), answer.get("confidence") or 0.0
    base = {"entity": entity, "choice": choice, "confidence": confidence}
    policy = by_option.get(choice)
    if policy is None:
        return {**base, "outcome": "needs-ruling", "why": "none of the policies fits"}
    if confidence < FLOOR:
        return {**base, "outcome": "needs-ruling", "why": f"confidence {confidence:.2f} < {FLOOR}"}
    tracker = tracked.get(policy)
    if tracker is None:
        return {**base, "outcome": "needs-ruling", "why": f"{policy} has no tracker yet"}
    at = now.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    subject = f"<{bb.A}{entity}>"
    turtle = (f"{subject} <{bb.A}governedBy> <{policy}> .\n"
              f"{subject} <{bb.A}trackedBy> <{tracker}> .\n"
              f"{subject} <{bb.A}inferredAt> \"{at}\"^^<{XSD_DT}> .\n")
    return {**base, "outcome": "propose", "policy": policy, "tracker": tracker, "turtle": turtle}


def propose(post, write, client, event: dict, now: dt.datetime | None = None) -> dict:
    """Deliver one directive-untraced event: write the proposal to crew/inferred,
    then read it back. Raises when the write cannot be confirmed, so the
    emitter retries; the retry is safe because a PROPOSED Directive is a noop."""
    now = now or dt.datetime.now(dt.timezone.utc)
    entity = event["item"]
    p = plan(post, client, entity, now)
    if p["outcome"] != "propose":
        return p
    write("/knot", {"turtle": p["turtle"], "actor": ACTOR, "graph": INFERRED,
                    "source": f"aegis-q9m5mp.47 directive-edges {event['event_id']} "
                              f"jev {_option(p['policy'])} {p['confidence']:.2f}"})
    after = judge(post, entity)["verdict"]
    if after == UNTRACED:
        raise ConnectionError(f"{entity}: proposal written but not read back (indeterminate)")
    return {**{k: v for k, v in p.items() if k != "turtle"}, "outcome": "proposed", "verdict": after}


_UNSAFE = set('<>"{}|\\^`')


def malformed(post, entity: str) -> list[tuple[str, str, str]]:
    """(prop, stored, expanded) for each traceability edge in crew/inferred whose
    object was stored as a compact name (`<a:x>`), not its full IRI (aegis-1e5).
    Raises on an object it cannot expand to the SAME target unambiguously."""
    out = []
    for prop in EDGES:
        for row in _rows(post, f"SELECT (STR(?o) AS ?so) WHERE {{ <{bb.A}{entity}> "
                               f"<{bb.A}{prop}> ?o . {MALFORMED} }}", INFERRED):
            stored = str(row["so"])
            full = _iri(stored)
            if (full == stored or not full.startswith(("http://", "https://"))
                    or any(c.isspace() or c in _UNSAFE for c in stored + full)):
                raise ValueError(f"{entity} {prop}: cannot expand {stored!r}; needs a ruling")
            out.append((prop, stored, full))
    return out


def repair_update(entity: str, fixes) -> str:
    """One same-graph compare-and-swap: close each malformed triple (quipu's
    DELETE is a bitemporal interval close, never an erase) and assert the same
    target as its full IRI. The WHERE makes a re-run or a raced repair a no-op."""
    subject = f"<{bb.A}{entity}>"
    old = " ".join(f"{subject} <{bb.A}{p}> <{stored}> ." for p, stored, _ in fixes)
    new = " ".join(f"{subject} <{bb.A}{p}> <{full}> ." for p, _, full in fixes)
    g = f"GRAPH <{INFERRED}>"
    return (f"DELETE {{ {g} {{ {old} }} }}\nINSERT {{ {g} {{ {new} }} }}\n"
            f"WHERE {{ {g} {{ {old} }} }}")


def repair(post, update, entity: str, apply: bool = False) -> dict:
    """Rewrite one Directive's malformed inferred edges as full IRIs, then read
    back. Same targets, same plane: ingress still never promotes its output."""
    fixes = malformed(post, entity)
    base = {"entity": entity, "fixes": [{"prop": p, "from": s, "to": f} for p, s, f in fixes]}
    if not fixes:
        return {**base, "outcome": "noop", "verdict": judge(post, entity)["verdict"]}
    if not is_directive(post, entity):
        return {**base, "outcome": "noop", "verdict": "NOT_A_DIRECTIVE"}
    body = {"update": repair_update(entity, fixes), "actor": ACTOR,
            "source": f"aegis-1e5 directive-edges repair {entity}"}
    if not apply:
        return {**base, "outcome": "would-repair", "update": body["update"]}
    result = update("/update", body)
    n = len(fixes)
    if (not isinstance(result, dict) or type(result.get("tx")) is not int
            or result.get("retracted") != n or result.get("asserted") != n):
        raise ConnectionError(f"{entity}: repair not confirmed ({result!r}); verify before retrying")
    left = malformed(post, entity)
    fixed = all(_has(post, entity, p, (INFERRED,)) for p, _, _ in fixes)
    if left or not fixed:
        raise ConnectionError(f"{entity}: repair tx {result['tx']} committed but read-back failed")
    return {**base, "outcome": "repaired", "tx": result["tx"],
            "verdict": judge(post, entity)["verdict"]}


def discover_malformed(post) -> list[str]:
    """Every subject with a malformed traceability edge in crew/inferred."""
    names = set()
    for prop in EDGES:
        for row in _rows(post, f"SELECT DISTINCT ?s WHERE {{ ?s <{bb.A}{prop}> ?o . "
                               f"{MALFORMED} }}", INFERRED):
            iri = _iri(str(row["s"]))
            if iri.startswith(bb.A):
                names.add(iri[len(bb.A):])
    return sorted(names)


def _post(endpoint: str, body: dict) -> dict:
    return planes._post(endpoint, body, client=CLIENT)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    # chaski runs `<command> --describe` or `<command> --changes` (incremental.py
    # call()), so each adapter view is a subcommand that takes those flags.
    for view in ("adapter", "lapse-adapter"):
        c = sub.add_parser(view, help="chaski's incremental protocol, JSON on stdin")
        c.add_argument("--describe", action="store_true")
        c.add_argument("--changes", action="store_true")
    j = sub.add_parser("judge")
    j.add_argument("entity")
    pr = sub.add_parser("propose", help="one event JSON on stdin")
    pr.add_argument("--dry-run", action="store_true")
    # Credentials as FILES, never argv or a launcher export (jev.py, aegis-6qau3t).
    pr.add_argument("--token-file", help="quipu bearer for the crew/inferred write")
    pr.add_argument("--jev-key-file", help="Jev key; else jev.py's own resolution")
    rp = sub.add_parser("repair", help="rewrite malformed a:-scheme inferred edges as full "
                                       "IRIs (aegis-1e5); a dry run unless --apply")
    rp.add_argument("entity", nargs="*", help="Directive names; default: every malformed one")
    rp.add_argument("--apply", action="store_true")
    rp.add_argument("--token-file", help="quipu bearer for the crew/inferred write")
    a = ap.parse_args(argv)
    if a.cmd in ("adapter", "lapse-adapter"):
        import change_adapter
        return change_adapter.main("directive" if a.cmd == "adapter" else "directive-lapse",
                                   describe=a.describe)
    if a.cmd == "judge":
        print(json.dumps(judge(_post, a.entity)))
        return 0
    if a.cmd == "repair":
        if a.token_file:
            with open(a.token_file) as f:
                planes.AUTH = f.read().strip() or None
        update = lambda path, body: planes._post(path, body, client=CLIENT, form=True)  # noqa: E731
        failed = 0
        for entity in a.entity or discover_malformed(_post):
            try:
                print(json.dumps(repair(_post, update, entity, apply=a.apply)))
            except (ValueError, ConnectionError, planes.PlaneError) as exc:
                failed += 1
                print(json.dumps({"entity": entity, "outcome": "failed", "why": str(exc)}))
        return 1 if failed else 0
    import os

    import jev
    if a.token_file:
        with open(a.token_file) as f:
            planes.AUTH = f.read().strip() or None
    if a.jev_key_file:
        os.environ[jev.KEY_FILE_ENV] = a.jev_key_file
    event = json.load(sys.stdin)
    client = jev.JevClient()
    if a.dry_run:
        out = plan(_post, client, event["item"], dt.datetime.now(dt.timezone.utc))
    else:
        out = propose(_post, _post, client, event)
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
