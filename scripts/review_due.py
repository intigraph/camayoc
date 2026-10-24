#!/usr/bin/env python3
"""Which graph entities are due to be re-checked, and which we cannot tell (aegis-kxjack).

Stiwi 2026-09-24: "check on this after this time and quipu will emit an event"
("age not agent"). Any entity may declare a review age:

  aegis:reviewAfter  an ISO-8601 instant; DUE from then on.
  aegis:maxAge       an ISO-8601 duration (PnW or PnDTnHnM), anchored on the
                     latest aegis:verifiedAt of a Verification that
                     aegis:verifies the entity. No such verification means
                     UNKNOWN: an unanchored age is neither fresh nor due.
  aegis:idleLimit    an ISO-8601 duration a tracked WorkItem may go without
                     tracker activity (aegis-qx96wr). Anchored on the observedAt
                     of its LATEST tracker Observation (aegis:observes), so real
                     activity pushes the expiry forward with no mutation. A
                     latest Observation saying closed or deferred RESOLVES it:
                     finished or deliberately parked work never fires. No
                     Observation means UNKNOWN.

An entity is DUE when any declared age has passed, NOT_DUE when every declared
age is known and still ahead, and UNKNOWN otherwise. A known overdue age wins
over an unknown one: something already overdue does not become "cannot tell"
because a second age is unanchored.

Each verdict is the adapter record an emitter consumes, the same contract as
blocked_by.py: entity, verdict, due_at (the earliest known due instant),
evidence (a stable digest of what decided it: audit identity, NOT a dedupe
key), owner (aegis:ownedBy, or null) and the per-age basis. This script holds
no memory between runs; transitions and delivery belong to the emitter.

Reads only, with single-pattern or bound-subject queries (joins exceed quipu's
10 s budget live), across the default graph and the observed and declared
planes, never the inferred one.

    python3 scripts/review_due.py              # JSON: one verdict per aged entity
    python3 scripts/review_due.py --due-only   # only the DUE ones
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import planes  # noqa: E402
from blocked_by import A, CLIENT, _local, item_iri, pattern, select  # noqa: E402

DUE, NOT_DUE, UNKNOWN = "DUE", "NOT_DUE", "UNKNOWN"
# The SAME grammar as CamayocReviewAgeShape: at least one component, and a T
# must be followed by hours or minutes. The write refuses what this cannot read.
_DURATION = re.compile(r"^P(?:(\d+)W|(\d+)D|(?:(\d+)D)?T(?:(\d+)H(?:(\d+)M)?|(\d+)M))$")


_FRACTION = re.compile(r"\.(\d+)")


def _iso_text(text: str) -> str:
    """ISO-8601 text that Python 3.10's fromisoformat accepts: Z as +00:00 and
    the fractional seconds as exactly six digits (truncated or padded).

    The tracker writes observedAt with NANOSECONDS (2026-10-01T13:10:47.150614617Z).
    Python 3.11+ parses that; 3.10 (the chaski host, measured 2026-10-08) does
    not, so every idleLimit anchor read as None, every planned WorkItem was
    UNKNOWN, and no lapse ever fired (aegis-qx96wr)."""
    text = text.replace("Z", "+00:00")
    return _FRACTION.sub(lambda m: "." + (m.group(1) + "000000")[:6], text, count=1)


def parse_instant(raw: str) -> dt.datetime | None:
    """An ISO-8601 date or offset instant as an aware UTC datetime, else None.
    A bare date means the start of that day, UTC."""
    text = raw.split("^^")[0].strip().strip('"')
    try:
        if len(text) == 10:
            return dt.datetime.fromisoformat(text).replace(tzinfo=dt.timezone.utc)
        value = dt.datetime.fromisoformat(_iso_text(text))
    except ValueError:
        return None
    return value.astimezone(dt.timezone.utc) if value.tzinfo else None


def parse_duration(raw: str) -> dt.timedelta | None:
    text = raw.split("^^")[0].strip().strip('"')
    m = _DURATION.match(text)
    if not m:
        return None
    weeks, days, t_days, hours, h_minutes, minutes = (int(g) if g else 0 for g in m.groups())
    return dt.timedelta(weeks=weeks, days=days + t_days, hours=hours, minutes=h_minutes + minutes)


def _values(post, entity: str, prop: str) -> list[str]:
    return [str(r["v"]) for r in select(post, f"SELECT ?v WHERE {{ {pattern(f'<{item_iri(entity)}>', prop, '?v')} }}")]


def last_verified(post, entity: str) -> dt.datetime | None:
    """Latest verifiedAt over Verifications that verify this entity: two bound
    single-pattern steps, never a join."""
    latest = None
    for row in select(post, f"SELECT ?ver WHERE {{ {pattern('?ver', 'verifies', f'<{item_iri(entity)}>')} }}"):
        for raw in _values(post, _local(row["ver"]), "verifiedAt"):
            when = parse_instant(raw)
            if when and (latest is None or when > latest):
                latest = when
    return latest


#: A latest tracker status that ends an idle obligation (aegis-qx96wr): closed
#: work is finished, and a deferral is a deliberate park with its own date.
RESOLVING_STATUS = {"closed", "deferred"}


def latest_activity(post, entity: str) -> tuple[dt.datetime | None, str | None]:
    """observedAt and observedStatus of the entity's LATEST tracker Observation.
    Bound single-pattern reads per Observation, never a join (as last_verified)."""
    latest, status = None, None
    for row in select(post, f"SELECT ?obs WHERE {{ {pattern(f'<{item_iri(entity)}>', 'observes', '?obs')} }}"):
        obs = _local(row["obs"])
        for raw in _values(post, obs, "observedAt"):
            when = parse_instant(raw)
            if when and (latest is None or when > latest):
                latest = when
                found = _values(post, obs, "observedStatus")
                status = found[0].strip('"') if found else None
    return latest, status


def _native_id(post, entity: str, reader=None) -> str | None:
    """Exact legacy identifier only, never a title or fuzzy about match."""
    import seed_work_items as native
    if entity.startswith(native.ITEM):
        from urllib.parse import unquote
        item_id=unquote(entity[len(native.ITEM):])
        if native.item_iri(item_id) != entity:
            raise native.NativeUnknown('native Action spelling is ambiguous')
        return item_id
    identifiers=_values(post,entity,'identifier')
    identifiers=[raw.strip('"') for raw in identifiers]
    if len(set(identifiers)) > 1:
        raise native.NativeUnknown('legacy WorkItem identifiers conflict')
    if identifiers:
        if reader is not None and entity.startswith(reader.board.prefix+'-') and entity != identifiers[0]:
            raise native.NativeUnknown('legacy exact-ID entity and identifier conflict')
        if reader is not None and not identifiers[0].startswith(reader.board.prefix+'-'):
            return None  # Other rigs retain their separately selected authority.
        return identifiers[0]
    if reader is not None and entity.startswith(reader.board.prefix+'-'):
        # Original canonical WorkItem names ARE exact tracker IDs. The native
        # Action's own identifier check still proves the destination separately.
        return entity
    return None


def judge(post, entity: str, now: dt.datetime, *, native_reader=None) -> dict:
    basis = []
    native_record=None
    native_failed=False
    native_error=None
    try:
        item_id=_native_id(post,entity,native_reader)
        if item_id is not None:
            if native_reader is None:
                from seed_work_items import deployed_reader
                native_reader=deployed_reader(post)
                if entity.startswith(native_reader.board.prefix+'-') and entity != item_id:
                    raise ValueError('legacy exact-ID entity and identifier conflict')
                if not item_id.startswith(native_reader.board.prefix+'-'):
                    item_id=None
            if item_id is not None:
                native_record=native_reader.get(item_id)
    except (ValueError,OSError,RuntimeError) as exc:
        native_failed=True
        native_error=type(exc).__name__
    for raw in _values(post, entity, "reviewAfter"):
        when = parse_instant(raw)
        basis.append({"age": "reviewAfter", "value": raw.strip('"'),
                      "due_at": when.isoformat() if when else None,
                      "why": "declared instant" if when else "unreadable instant"})
    for raw in _values(post, entity, "maxAge"):
        span = parse_duration(raw)
        anchor = last_verified(post, entity) if span else None
        when = anchor + span if anchor and span else None
        why = ("unreadable duration" if span is None else
               "no Verification verifies it, so the age has no anchor" if anchor is None else
               f"last verified {anchor.isoformat()}")
        basis.append({"age": "maxAge", "value": raw.strip('"'),
                      "due_at": when.isoformat() if when else None, "why": why})
    for raw in _values(post, entity, "idleLimit"):
        span = parse_duration(raw)
        if native_record is not None:
            from seed_work_items import instant
            anchor,status=instant(native_record.modified),native_record.status
        elif native_failed:
            anchor,status=None,None  # Failed native authority cannot use stale Observation.
        else:
            anchor,status=latest_activity(post,entity) if span else (None,None)
        resolved = status in RESOLVING_STATUS
        when = anchor + span if anchor and span and not resolved else None
        why = ("unreadable duration" if span is None else
               f"native authority UNKNOWN ({native_error}); no legacy fallback" if native_failed else
               "no tracker Observation, so the idle age has no anchor" if anchor is None else
               f"resolved: the latest tracker status is {status}" if resolved else
               f"last native activity {anchor.isoformat()}" if native_record is not None else
               f"last tracker activity {anchor.isoformat()}")
        basis.append({"age": "idleLimit", "value": raw.strip('"'),
                      "due_at": when.isoformat() if when else None, "why": why,
                      **({"resolved": True} if resolved else {})})
    known = [dt.datetime.fromisoformat(b["due_at"]) for b in basis if b["due_at"]]
    settled = len(known) + sum(1 for b in basis if b.get("resolved"))
    if any(t <= now for t in known):
        verdict = DUE
    elif basis and settled == len(basis):
        verdict = NOT_DUE
    else:
        verdict = UNKNOWN
    # Classification is context for the receiver's configured policy, never
    # adapter-supplied severity. Ambiguous kinds keep the ordinary policy.
    kinds = {raw.strip('"') for raw in _values(post, entity, "workKind")}
    work_kind = next(iter(kinds)) if len(kinds) == 1 else None
    if native_record is not None:
        owners=[native_record.owner] if native_record.owner else []
    elif native_failed:
        owners=[]
    else:
        owners = [_local(r["v"]) for r in select(post, f"SELECT ?v WHERE {{ {pattern(f'<{item_iri(entity)}>', 'ownedBy', '?v')} }}")]
    native_source=({'graph':native_record.graph,'action':native_record.action,'id':native_record.id,
                    'version':native_record.version,'modified':native_record.modified,
                    'status':native_record.status} if native_record is not None else None)
    identity=[entity, sorted((b["age"], b["value"], b["due_at"] or "") for b in basis)]
    if native_source is not None or native_failed:
        identity.append(native_source or {'native':'UNKNOWN'})
    ident = json.dumps(identity,
                       separators=(",", ":"))
    passed = [t for t in known if t <= now]
    # The 'due' event's id (aegis-kxjack; sattler's delivery ruling on
    # aegis-2qo001): DETERMINISTIC from (entity, the due instant it crossed),
    # never minted at send time, so a re-emit after send-then-crash dedupes.
    # A re-verification moves a maxAge anchor, so a later lapse is a new id.
    event = (json.dumps([item_iri(entity), min(passed).isoformat()], separators=(",", ":"))
             if verdict == DUE else None)
    return {"entity": entity, "verdict": verdict,
            **({"work_kind": work_kind} if work_kind else {}),
            **({"event_id": "sha256:" + hashlib.sha256(event.encode()).hexdigest()} if event else {}),
            "due_at": min(known).isoformat() if known else None,
            "evidence": "sha256:" + hashlib.sha256(ident.encode()).hexdigest(),
            "owner": sorted(owners)[0] if owners else None, "basis": basis,
            **({'native_source':native_source} if native_source is not None else {}),
            **({'native_authority':'UNKNOWN'} if native_failed else {})}


def evaluate(post, *, now: dt.datetime | None = None) -> list[dict]:
    now = now or dt.datetime.now(dt.timezone.utc)
    aged = set()
    for prop in ("reviewAfter", "maxAge", "idleLimit"):
        for row in select(post, f"SELECT ?s WHERE {{ {pattern('?s', prop, '?v')} }}"):
            aged.add(_local(row["s"]))
    return [judge(post, e, now) for e in sorted(aged)]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--due-only", action="store_true")
    parser.add_argument("--changes", action="store_true", help="incremental JSON protocol on stdin")
    parser.add_argument("--describe", action="store_true", help="print change subscription, no graph reads")
    args = parser.parse_args(argv)
    if args.changes or args.describe:
        import change_adapter
        return change_adapter.main("review", describe=args.describe)
    post = lambda endpoint, body: planes._post(endpoint, body, client=CLIENT)  # noqa: E731
    rows = evaluate(post)
    if args.due_only:
        rows = [r for r in rows if r["verdict"] == DUE]
    print(json.dumps(rows, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
