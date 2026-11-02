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

TRACED, PROPOSED, UNTRACED = "TRACED", "PROPOSED", "UNTRACED"
#: The lapse view (sattler's ruling (a), [ian-q47-grace-window-blocker]): the
#: shape stays Warning at write time, and a Directive still untraced, or still
#: awaiting promotion, after GRACE is LAPSED and reaches a person.
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
    out = post("/query", {"query": query, **({"graph": graph} if graph else {})})
    if not isinstance(out.get("rows"), list) or out.get("truncated"):
        raise ValueError(f"query unproven in {graph or 'default'}: {query[:80]}")
    return out["rows"]


def _iri(term: str) -> str:
    """Expand Quipu compact IRIs using the shared reader contract."""
    return bb._iri(str(term))


def _has(post, entity: str, prop: str, graphs) -> bool:
    q = f"SELECT ?o WHERE {{ <{bb.A}{entity}> <{bb.A}{prop}> ?o }}"
    return any(_rows(post, q, g) for g in graphs)


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


def judge(post, entity: str, now: dt.datetime | None = None) -> dict:
    """Both views of one Directive. `verdict` drives the proposer; `lapse`
    drives the alert. A TRACED Directive never lapses."""
    now = now or dt.datetime.now(dt.timezone.utc)
    traced = all(_has(post, entity, p, (None, DECLARED)) for p in EDGES)
    proposed = not traced and all(_has(post, entity, p, (INFERRED,)) for p in EDGES)
    verdict = TRACED if traced else PROPOSED if proposed else UNTRACED
    start = None if verdict == TRACED else anchor(post, entity, verdict)
    due = start + GRACE if start else None
    # UNANCHORED counts as LAPSED (wu, [wu-cm84-review] item 1): measured, 407 of
    # 868 Directives have no datable capture episode. A stateless adapter cannot
    # remember first sight, so an untraced Directive with no age reaches a person
    # NOW rather than never.
    unanchored = verdict != TRACED and start is None
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
                                        f"{start.isoformat() if start else 'unanchored'}")
                               if lapse == LAPSED else None),
            "due_at": due.isoformat() if due else None,
            "evidence": verdict, "owner": None}


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
    verdict = judge(post, entity)["verdict"]
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
    a = ap.parse_args(argv)
    if a.cmd in ("adapter", "lapse-adapter"):
        import change_adapter
        return change_adapter.main("directive" if a.cmd == "adapter" else "directive-lapse",
                                   describe=a.describe)
    if a.cmd == "judge":
        print(json.dumps(judge(_post, a.entity)))
        return 0
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
