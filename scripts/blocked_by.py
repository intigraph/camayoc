#!/usr/bin/env python3
"""Which work is blocked, which just became unblocked, and which we cannot tell (aegis-c0awwp).

`aegis:blockedOn` points a WorkItem at what it waits on: another WorkItem (a
tracker dependency) or a Blocker condition with a checkable resolution. Whether
a blocker still holds is evaluated HERE, at read time, and never stored:

  WorkItem target   resolved when its current status is closed: the latest
                    Observation's observedStatus is "closed", or the WorkItem
                    carries aegis:closedAt. No status at all -> unknown.
  Blocker, date     resolved from aegis:resolvesOn onward.
  Blocker, query    resolved when its aegis:resolutionQuery ASK answers true;
                    false -> unresolved; an error or a malformed answer -> unknown.
  Blocker, neither  unknown: a stated reason nobody can check.

A work item is UNBLOCKED when every target is resolved, BLOCKED when any is
unresolved, and UNKNOWN otherwise. "Could not tell" never rounds to
"unblocked": that is the one direction an event consumer would act on.
Items whose own current status is closed are left out.

Reads only, with SINGLE-PATTERN queries (any join, even a bound-subject one,
exceeded quipu's 10 s budget live), across
the default, observed-records, declared, and configured seeds board graphs.
Each evaluation resolves a WorkItem's latest observation once, including an
absent history. The cache belongs to that evaluation; the next scheduled run
reads the graph again. Verdict reads use the chaski-adapter client label,
separate from tracker ingestion.

    python3 scripts/blocked_by.py            # JSON: one verdict per blocked item
    python3 scripts/blocked_by.py --item aegis-bgk9ho
    python3 scripts/blocked_by.py --item https://seeds.local/item/sd-example

CAMAYOC_BOARD_GRAPHS is a comma-separated board graph list, defaulting to
https://seeds.local/project/aegis. An empty value disables board reads. Seed
keys are their stored full IRIs, preserving percent encoding; their native
seeds:status wins over close metadata so reopening takes effect immediately.

Each verdict is the adapter record an emitter consumes (aegis-2qo001): item,
verdict (BLOCKED / UNBLOCKED / UNKNOWN), evidence (a stable digest of the
blocker targets and their states), assignee, and the blockers. This script
holds no memory between runs: transitions, baselines and delivery belong to
the emitter, which must treat UNKNOWN as "keep the last known verdict".
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parent))

import planes  # noqa: E402
from ingest_work_items import BASE_NS  # noqa: E402

CLIENT = "chaski-adapter"
A = BASE_NS
SEED_ITEM = "https://seeds.local/item/"
SEED_STATUS = "https://seeds.local/ontology/status"
SCHEMA = "https://schema.org/"
TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"

# The quechua transition (aegis-9dpcta). The served quipu does NOT honour
# owl:equivalentClass on reads (measured 0/4 cross-reads vs 1/4 controls), so a
# reader names both IRIs itself: every term that has a quechua twin is read
# under the legacy aegis: IRI AND the public one. The pairs come from ian's
# transition map (aegis-9dpcta PR124 a5467313, complete-shape-term-map.json).
# Terms the map does not carry (closedAt, observes, verifies, verifiedAt) stay
# legacy-only: a pair nobody published is a guess, not a mapping. Instance IRIs
# never move, only terms do.
#
# UNION, not a property-path alternation: the served quipu's (p1|p2) drops
# every literal-object match (measured 0 vs 1 on reviewAfter, aegis-sxlptn).
Q = "https://scbrown.github.io/quechua/ns#"
DUAL = frozenset({
    "blockedOn", "observedBlockedOn", "observedAt", "observedStatus", "observedValue", "blockerKind",
    "prRef", "tool", "minVersion", "repoRef", "resolvesOn", "resolutionQuery",
    "reviewAfter", "maxAge", "ownedBy", "WorkItem", "Blocker",
})


def term_iris(name: str) -> list[str]:
    """Every IRI a reader must accept for one term, legacy first."""
    return [A + name, Q + name] if name in DUAL else [A + name]


def pattern(subject: str, name: str, obj: str) -> str:
    """One triple pattern per IRI of `name`, as a UNION of single patterns.
    Each branch stays one bound pattern, so the never-a-join rule holds."""
    branches = [f"{subject} <{iri}> {obj}" for iri in term_iris(name)]
    if len(branches) == 1:
        return branches[0]
    return " UNION ".join("{ " + b + " }" for b in branches)
RESOLVED, UNRESOLVED, UNKNOWN = "resolved", "unresolved", "unknown"
BLOCKED, UNBLOCKED = "BLOCKED", "UNBLOCKED"


class _EvaluationReads:
    """One run's transport and history resolutions, never shared across runs."""

    def __init__(self, post, batch_history=False):
        self.post = post
        self.batch_history = batch_history
        self.latest: dict[str, str | None] = {}

    def __call__(self, endpoint, body):
        return self.post(endpoint, body)


def graphs() -> list[str | None]:
    """Where blocking facts live: the default graph, the observed records (the
    tracker projection), seeds boards, and the DECLARED plane (conditions
    someone declared). Not the inferred plane: an inferred blocker is quarantined
    guesswork and must not hold work up."""
    boards = [g.strip() for g in os.environ.get(
        "CAMAYOC_BOARD_GRAPHS", "https://seeds.local/project/aegis").split(",") if g.strip()]
    if planes.plane_for("inferred") in boards:
        raise ValueError("the inferred plane is not a board graph; refusing")
    return list(dict.fromkeys([None, planes.plane_for("observed"),
                               planes.plane_for("declared"), *boards]))


def _iri(term: str) -> str:
    if term.startswith("aegis:"):
        return A + term[len("aegis:"):]
    if term.startswith("schema:"):
        return SCHEMA + term[len("schema:"):]
    return term


def item_iri(item: str) -> str:
    """Preserve stored seed IRIs; legacy adapter keys remain local names."""
    iri = _iri(item) if item.startswith(("https://", "http://", "aegis:")) else A + item
    if any(c in iri for c in '<>"{}\\\n\r\t '):
        raise ValueError("unsafe entity IRI")
    return iri


def seed_value(post, item: str, predicate: str) -> str | None:
    rows = select(post, f"SELECT ?v WHERE {{ <{item_iri(item)}> <{predicate}> ?v }}")
    if len(rows) != 1:
        return None
    return str(rows[0]["v"]).split("^^")[0].strip('"')


def _local(term: str) -> str:
    return _iri(term)[len(A):] if _iri(term).startswith(A) else term


def select(post, query: str) -> list[dict]:
    """Rows from every graph a record can live in, de-duplicated."""
    seen, rows = set(), []
    for graph in graphs():
        result = post("/query", {"query": query, **({"graph": graph} if graph else {})})
        if not isinstance(result.get("rows"), list) or result.get("truncated"):
            raise ValueError(f"query unproven in {graph or 'default'}: {query[:80]}")
        for row in result["rows"]:
            key = json.dumps(row, sort_keys=True)
            if key not in seen:
                seen.add(key)
                rows.append(row)
    return rows


def current_status(post, item: str) -> str | None:
    """The tracker status of a WorkItem from its LATEST Observation; "closed"
    also when the WorkItem carries aegis:closedAt; None when nothing says."""
    if item.startswith(SEED_ITEM):
        return seed_value(post, item, SEED_STATUS)
    if select(post, f"SELECT ?c WHERE {{ {pattern(f'<{A}{item}>', 'closedAt', '?c')} }}"):
        return "closed"
    obs = latest_observation(post, item)
    if obs is None:
        return None
    rows = select(post, f"SELECT ?st WHERE {{ {pattern(f'<{A}{obs}>', 'observedStatus', '?st')} }}")
    if rows:
        return str(rows[0]["st"]).strip('"')
    # Older Observations carry status only inside their JSON snapshot.
    return snapshot(post, obs).get("status")


def snapshot(post, obs: str) -> dict:
    """The tracker record an Observation captured (its observedValue JSON), or {}."""
    rows = select(post, f"SELECT ?v WHERE {{ {pattern(f'<{A}{obs}>', 'observedValue', '?v')} }}")
    try:
        raw = str(rows[0]["v"])
        raw = raw[1:-1].encode().decode("unicode_escape") if raw.startswith('"') else raw
        value = json.loads(raw)
    except (IndexError, ValueError, AttributeError):
        return {}
    return value if isinstance(value, dict) else {}


def current_assignee(post, item: str) -> str | None:
    """Who the tracker says holds the item now (latest Observation), or None."""
    if item.startswith(SEED_ITEM):
        agent = seed_value(post, item, SCHEMA + "agent")
        prefix = "https://seeds.local/principal/"
        return unquote(agent[len(prefix):]) if agent and agent.startswith(prefix) else agent
    obs = latest_observation(post, item)
    return (snapshot(post, obs).get("assignee") or None) if obs else None


def evidence_id(item: str, judged: list[tuple[str, str]]) -> str:
    """A stable identity for the evidence behind one verdict: the item and each
    blocker target with its state. The same facts give the same id on every
    run; any blocker changing state changes it. It is AUDIT identity (which
    facts produced this verdict), not a dedupe key: the emitter keys its
    outbox on (item, generation), because UNKNOWN between two identical
    states must not mint a second event."""
    basis = json.dumps([item, sorted(judged)], separators=(",", ":"))
    return "sha256:" + hashlib.sha256(basis.encode()).hexdigest()


def event_id(post, item: str, names: list[str], judged: list[tuple[str, str]]) -> str:
    """The id of the 'unblocked' event this verdict would cause (aegis-2qo001).

    sattler's ruling (2026-09-25): delivery is at-least-once with an idempotent
    receiver that dedupes on this id, so it must be DETERMINISTIC from (the
    WorkItem, the transition that caused it), never minted at send time. A
    re-emit after a send-then-crash must carry the same id or the receiver's
    dedupe is vacuous in exactly the case it exists for.

    Seeds use their native schema:dateModified instant for the closing version.
    The causing transition is each blocker's RESOLVING FACT. For a WorkItem
    blocker that is its latest Observation, which is content-addressed and so
    differs for a close, a reopen and a second close: a genuine later
    unblock is a NEW id, a replay of the same one is the SAME id. For a typed
    Blocker it is the evaluated condition (kind, parameters, answer), which is
    already deterministic.
    """
    facts = []
    for target, (state, why) in zip(names, judged):
        fact = latest_observation(post, target) if why == f"{target}: closed" else None
        facts.append([target, fact or why])
    basis = json.dumps([item_iri(item), sorted(facts)], separators=(",", ":"))
    return "sha256:" + hashlib.sha256(basis.encode()).hexdigest()


def latest_observation(post, item: str) -> str | None:
    """Latest tracker Observation, or a seed's native modification instant."""
    if isinstance(post, _EvaluationReads):
        if item not in post.latest:
            post.latest[item] = _read_latest_observation(post, item)
        return post.latest[item]
    return _read_latest_observation(post, item)


def _read_latest_observation(post, item: str) -> str | None:
    if item.startswith(SEED_ITEM):
        return seed_value(post, item, SCHEMA + "dateModified")
    # Two BOUND single-pattern queries, never a join: quipu plans even a
    # bound-subject two-pattern query from its unbound side and 408s it live.
    observations = [_local(row["obs"]) for row in select(post,
                    f"SELECT ?obs WHERE {{ {pattern(f'<{A}{item}>', 'observes', '?obs')} }}")]
    stamped = []
    if isinstance(post, _EvaluationReads) and post.batch_history:
        # Each UNION branch is still ONE bound pattern. Distinct output variables
        # identify the observation without BIND, VALUES or a graph join. Keep the
        # legacy evaluator unbatched as an independent parity oracle.
        for start in range(0, len(observations), 32):
            batch = observations[start:start + 32]
            branches = [f"{{ <{A}{obs}> <{iri}> ?at{i} }}" for i, obs in enumerate(batch)
                        for iri in term_iris("observedAt")]
            columns = " ".join(f"?at{i}" for i in range(len(batch)))
            rows = select(post, f"SELECT {columns} WHERE {{ {' UNION '.join(branches)} }}")
            for i, obs in enumerate(batch):
                first = next((r[f"at{i}"] for r in rows if r.get(f"at{i}") is not None), None)
                if first is not None:
                    stamped.append((str(first), obs))
    else:
        for obs in observations:
            at = select(post, f"SELECT ?at WHERE {{ {pattern(f'<{A}{obs}>', 'observedAt', '?at')} }}")
            if at:
                stamped.append((str(at[0]["at"]), obs))
    if not stamped:
        return None
    newest = max(at for at, _ in stamped)
    tied = sorted(obs for at, obs in stamped if at == newest)
    if len(tied) == 1:
        return tied[0]
    # observedAt is the tracker's updated_at, so two Observations share it only
    # when the SAME tracker state was projected twice by different code: the
    # one minted after the projection landed carries observedStatus, the older
    # one does not. br bumps updated_at on dep add/remove (measured), so a
    # dependency change never ties. Without this the name decided, and a bead
    # untouched since the projection read as having no status and no blockers
    # (aegis-3b3nrb, measured on aegis-sfpfwf).
    projected = [obs for obs in tied
                 if select(post, f"SELECT ?st WHERE {{ {pattern(f'<{A}{obs}>', 'observedStatus', '?st')} }}")]
    return (projected or tied)[0]


# ---- typed probes: FIXED code, parameters from the graph (aegis-c0awwp) ----
# Every probe returns True (resolved), False (unresolved) or None (could not
# tell). Parameters are re-validated here, independent of the SHACL shape, and
# nothing is ever run through a shell.
import re
import subprocess

_PR = re.compile(r"^([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)#([0-9]+)$")
_TOOL = re.compile(r"^[A-Za-z0-9_.-]+$")
_VER = re.compile(r"^[0-9]+(\.[0-9]+)*$")
_REPO = re.compile(r"^([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)@([A-Za-z0-9_./-]+)$")


def _run(argv: list[str]) -> str | None:
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout if out.returncode == 0 else None


def probe_pr_merged(ref: str):
    m = _PR.match(ref)
    if not m:
        return None
    out = _run(["gh", "api", f"repos/{m[1]}/{m[2]}/pulls/{m[3]}", "--jq", ".merged"])
    return None if out is None else out.strip() == "true"


def _version_tuple(text: str):
    m = re.search(r"([0-9]+(?:\.[0-9]+)+)", text or "")
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def probe_release_installed(tool: str, min_version: str):
    if not _TOOL.match(tool) or not _VER.match(min_version):
        return None
    have = _version_tuple(_run([tool, "--version"]) or "")
    if have is None:
        return None
    want = tuple(int(x) for x in min_version.split("."))
    width = max(len(have), len(want))
    return have + (0,) * (width - len(have)) >= want + (0,) * (width - len(want))


def probe_ci_green(ref: str):
    m = _REPO.match(ref)
    if not m:
        return None
    out = _run(["gh", "run", "list", "-R", f"{m[1]}/{m[2]}", "-b", m[3], "--status", "completed",
                "-L", "1", "--json", "conclusion", "--jq", ".[0].conclusion"])
    return None if not out or not out.strip() else out.strip() == "success"


PROBES = {"pr-merged": probe_pr_merged, "release-installed": probe_release_installed,
          "ci-green": probe_ci_green}


def _prop(post, target: str, name: str) -> str | None:
    rows = select(post, f"SELECT ?v WHERE {{ {pattern(f'<{A}{target}>', name, '?v')} }}")
    return str(rows[0]["v"]).split("^^")[0].strip('"') if rows else None


def blocker_state(post, target: str, today: dt.date, probes=None) -> tuple[str, str]:
    """(state, why) for one blockedOn target."""
    types = {_iri(r["t"]) for r in select(post, f"SELECT ?t WHERE {{ <{item_iri(target)}> a ?t }}")}
    if types & (set(term_iris("WorkItem")) | {SCHEMA + "Action"}):
        status = current_status(post, target)
        if status is None:
            return UNKNOWN, f"{target}: no tracker status in the graph"
        return (RESOLVED if status in ("closed", "tombstone") else UNRESOLVED), f"{target}: {status}"
    if types & set(term_iris("Blocker")):
        probes = PROBES if probes is None else probes
        kind = _prop(post, target, "blockerKind")
        if kind in ("pr-merged", "release-installed", "ci-green"):
            args = {"pr-merged": ["prRef"], "release-installed": ["tool", "minVersion"],
                    "ci-green": ["repoRef"]}[kind]
            values = [_prop(post, target, a) for a in args]
            if None in values:
                return UNKNOWN, f"{target}: {kind} blocker missing {args}"
            answer = probes[kind](*values)
            if answer is None:
                return UNKNOWN, f"{target}: {kind} {' '.join(values)} could not be checked"
            return (RESOLVED if answer else UNRESOLVED), f"{target}: {kind} {' '.join(values)} -> {answer}"
        dates = select(post, f"SELECT ?d WHERE {{ {pattern(f'<{A}{target}>', 'resolvesOn', '?d')} }}")
        asks = select(post, f"SELECT ?q WHERE {{ {pattern(f'<{A}{target}>', 'resolutionQuery', '?q')} }}")
        if dates:
            raw = str(dates[0]["d"]).split("^^")[0].strip('"')
            try:
                when = dt.date.fromisoformat(raw[:10])
            except ValueError:
                return UNKNOWN, f"{target}: unreadable resolvesOn {raw!r}"
            return (RESOLVED if today >= when else UNRESOLVED), f"{target}: date {when}"
        if asks:
            ask = str(asks[0]["q"]).strip('"').encode().decode("unicode_escape")
            try:
                answer = post("/query", {"query": ask})
            except Exception as error:  # an ASK we could not run is not an answer
                return UNKNOWN, f"{target}: resolution query failed ({type(error).__name__})"
            if isinstance(answer.get("result"), bool):
                return (RESOLVED if answer["result"] else UNRESOLVED), f"{target}: ASK {answer['result']}"
            return UNKNOWN, f"{target}: resolution query gave no boolean"
        return UNKNOWN, f"{target}: a Blocker with no checkable resolution"
    return UNKNOWN, f"{target}: not a WorkItem or Blocker in the graph"


def verdict(states: list[str]) -> str:
    if states and all(s == RESOLVED for s in states):
        return UNBLOCKED
    if any(s == UNRESOLVED for s in states):
        return BLOCKED
    return UNKNOWN.upper()


def evaluate(post, *, item: str | None = None, today: dt.date | None = None,
             probes=None, batch_history=False) -> list[dict]:
    # Historical dependency rows can repeat the same WorkItem many times.
    # All consumers (status, assignee and event identity too) share this run's
    # resolution, so each history is walked once, not once per historical row.
    post = _EvaluationReads(post, batch_history=batch_history)
    today = today or dt.date.today()
    scope = f"<{item_iri(item)}>" if item else "?w"
    # Declared by an agent on the WorkItem ...
    edges = select(post, f"SELECT {'?w ' if not item else ''}?t WHERE {{ {pattern(scope, 'blockedOn', '?t')} }}")
    by_item: dict[str, list[str]] = {}
    for row in edges:
        w = item or _local(row["w"])
        by_item.setdefault(w, []).append(_local(row["t"]))
    # ... and projected from the tracker onto the LATEST Observation only, so a
    # removed dependency (absent from the newest Observation) no longer blocks.
    # Single-pattern or bound-subject queries only: the two-pattern join over
    # the whole store exceeded quipu's 10 s budget live (HTTP 408).
    if item and not item.startswith(SEED_ITEM):
        latest = latest_observation(post, item)
        if latest:
            for row in select(post, f"SELECT ?t WHERE {{ {pattern(f'<{A}{latest}>', 'observedBlockedOn', '?t')} }}"):
                by_item.setdefault(item, []).append(_local(row["t"]))
    elif not item:
        owner: dict[str, str | None] = {}
        for row in select(post, f"SELECT ?obs ?t WHERE {{ {pattern('?obs', 'observedBlockedOn', '?t')} }}"):
            obs = _local(row["obs"])
            if obs not in owner:
                found = select(post, f"SELECT ?w WHERE {{ {pattern('?w', 'observes', f'<{A}{obs}>')} }}")
                owner[obs] = _local(found[0]["w"]) if found else None
            w = owner[obs]
            if w and latest_observation(post, w) == obs:
                by_item.setdefault(w, []).append(_local(row["t"]))
    out = []
    for w, targets in sorted(by_item.items()):
        if current_status(post, w) in ("closed", "tombstone"):
            continue
        names = sorted(set(targets))
        judged = [blocker_state(post, t, today, probes) for t in names]
        state = verdict([s for s, _ in judged])
        out.append({"item": w, "verdict": state,
                    **({"event_id": event_id(post, w, names, judged)} if state == UNBLOCKED else {}),
                    "evidence": evidence_id(w, [(t, s) for t, (s, _) in zip(names, judged)]),
                    "assignee": current_assignee(post, w),
                    "blockers": [{"target": t, "state": s, "why": why}
                                 for t, (s, why) in zip(names, judged)]})
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--item", help="one work item id (e.g. aegis-bgk9ho)")
    parser.add_argument("--changes", action="store_true", help="incremental JSON protocol on stdin")
    parser.add_argument("--describe", action="store_true", help="print change subscription, no graph reads")
    args = parser.parse_args(argv)
    if args.changes or args.describe:
        import change_adapter
        return change_adapter.main("blocked", describe=args.describe)
    post = lambda endpoint, body: planes._post(endpoint, body, client=CLIENT)  # noqa: E731
    print(json.dumps(evaluate(post, item=args.item), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
