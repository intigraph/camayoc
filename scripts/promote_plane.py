#!/usr/bin/env python3
"""Authority-gated plane promotion — camayoc-mip.

Promotion is how a fact **earns** its way out of quarantine: a governed move
from `crew:inferred` into a plane that outranks it. It is deliberately not an
ingress feature (`docs/design/ingress.md` §4): ingress never upgrades its own
output, because a writer that can promote what it wrote has quarantine in name
only.

## The name

`shapes/code-entities.ttl` already uses "promotion" for an unrelated
bobbin-to-quipu ingest path. That name is established there, so this mechanism
is **plane promotion** throughout — the command is `promote-plane`, the
recorded fact is `camayoc:planePromotion`. Two different governed operations
sharing one word in one repo reads fine to whoever wrote it and confuses every
later reader.

## What it refuses, and why each refusal exists

- **No authority over the target plane.** Fails closed: an absent or unreadable
  authority file means *nobody* may promote, not everybody. A governance
  mechanism that defaults open is decoration.
- **Self-promotion.** The principal promoting must differ from the principal
  that wrote the fact. This is the rule `skills/camayoc/SKILL.md` states to
  agents in prose ("promotable later by someone with authority — never by you
  up-tagging it"); here it is enforced rather than requested.
- **Promotion into a plane that does not outrank the source.** A sideways or
  downward "promotion" is a category error, and one that would read in the
  audit record as if trust had been earned.
- **Promotion of something not actually in the source plane.** Otherwise the
  record asserts a move that never happened.

## The move rule (camayoc-913, resolved with quipu deep freeze)

**Assert in the target, CLOSE in the source, record the move.** The close is
a bitemporal retraction — quipu closes the valid interval, never deletes —
so the original stays visible as-of its write time in the inferred plane
(competency/workflow-and-archive.md Q13). The same rule governs deep freeze,
where the "close" is whole-graph relocation with the full-history pack as
the surviving record. The worry that closing erases the low-trust past was
the reason this stayed open; bitemporality answers it: valid-time replay
still shows the fact where and when it lived.

The close operates on the SOURCE EPISODE (`--source-episode`), because the
episode is the unit ingress actually writes into the plane and quipu's
`POST /episode/retract` is the graph-honest retraction surface (triple-level
`/retract` is ROOT-scoped). When the caller cannot name the source episode
the promotion still proceeds, the source interval stays open, and the
promotion record says so out loud (`camayoc:sourceLeftOpen true`) — an open
interval on the record beats a close that silently never happened.

## Exact target supersession

`--supersedes old.nt` replaces ONE current target fact while promoting ONE
inferred replacement with the same subject/predicate. The usual authority and
no-self-promotion gates still apply. A conditional `/update` rechecks both facts
and refuses competing target values under the writer lock. It closes the old
valid interval and writes the replacement and audit record in ONE target-graph
transaction. The carrier episode and other target facts are never retracted.
The inferred source remains open, explicitly recorded. `--source-episode` is
incompatible. Store valid-time uses commit wall time; `--timestamp` dates the
promotion record, not a backdated target close. Missing/changed preconditions
refuse without a write; an indeterminate response must be verified, not retried.

Usage:
    python3 scripts/promote_plane.py \\
        --triples-file edges.nt --to crew:records \\
        --by alice --authored-by claude --reason "corroborated by the deploy log"
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _load_planes():
    spec = importlib.util.spec_from_file_location("planes", ROOT / "planes.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    # Registered before exec: @dataclass resolves annotations through
    # sys.modules, and a module absent from it fails with an opaque
    # NoneType error rather than an import error.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


planes = _load_planes()

#: Where the authority grants live. A human-maintained file: who may promote
#: into which planes. Deliberately NOT derivable from anything an agent writes.
AUTHORITY_PATH = Path(
    os.environ.get("CAMAYOC_AUTHORITY", ROOT.parent / "config" / "plane-authority.json")
)

#: The plane facts are promoted OUT of. Promotion is only ever out of
#: quarantine; moving between the high-trust planes is a different operation
#: and is not this one.
SOURCE_PLANE = "crew:inferred"


class PromotionRefused(RuntimeError):
    """A promotion was refused. Always raised, never returned as a soft verdict —
    a refused promotion that returns a result object gets treated as a result."""


def load_authority() -> dict[str, list[str]]:
    """Principal -> planes they may promote into.

    Fails CLOSED. A missing, unreadable, or malformed file yields no grants,
    so every promotion is refused. The alternative — treating an unreadable
    grant file as unrestricted — is the failure mode that makes an authority
    check worthless.
    """
    try:
        raw = json.loads(AUTHORITY_PATH.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise PromotionRefused(
            f"authority file {AUTHORITY_PATH} could not be read ({exc}); refusing all "
            "promotion. This is 'could not look', and it fails closed on purpose."
        ) from exc
    if not isinstance(raw, dict):
        raise PromotionRefused(f"authority file {AUTHORITY_PATH} is not an object")
    return {k: list(v) for k, v in raw.items() if isinstance(v, list)}


def check_authority(principal: str, target_plane: str, grants: dict[str, list[str]]) -> None:
    if target_plane not in grants.get(principal, []):
        raise PromotionRefused(
            f"'{principal}' holds no authority over '{target_plane}'. "
            f"Grants live in {AUTHORITY_PATH} and are human-maintained."
        )


def check_not_self_promotion(promoted_by: str, authored_by: str) -> None:
    if promoted_by == authored_by:
        raise PromotionRefused(
            f"'{promoted_by}' wrote this fact and cannot promote it. Quarantine that "
            "the writer can leave on its own authority is quarantine in name only."
        )


def check_outranks(target_plane: str) -> None:
    if target_plane not in planes.PLANES:
        raise PromotionRefused(f"unknown target plane '{target_plane}'")
    source_rank = planes.PLANES[SOURCE_PLANE]["rank"]
    target_rank = planes.PLANES[target_plane]["rank"]
    if target_rank <= source_rank:
        raise PromotionRefused(
            f"'{target_plane}' (rank {target_rank}) does not outrank "
            f"'{SOURCE_PLANE}' (rank {source_rank}); a sideways or downward move is "
            "not a promotion and must not be recorded as one."
        )


#: One N-Triples term: an IRI, or a literal (with optional datatype/language).
_TERM = r'(<[^<>\s]+>|"(?:[^"\\]|\\.)*"(?:\^\^<[^<>\s]+>|@[A-Za-z0-9-]+)?)'
_TRIPLE = re.compile(rf"^\s*(<[^<>\s]+>)\s+(<[^<>\s]+>)\s+{_TERM}\s*\.\s*$")

Triple = tuple[str, str, str]


def parse_ntriples(text: str) -> list[Triple]:
    """Exact edges to promote, one N-Triples statement per line.

    The fact is an EDGE, not a subject (aegis-is257g): a subject can carry
    several inferred edges, and promotion must be able to move exactly the ones
    that earned it. Blank lines and `#` comments are skipped; anything else that
    is not one triple is refused rather than guessed at.
    """
    triples: list[Triple] = []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = _TRIPLE.match(line)
        if not m:
            raise PromotionRefused(f"line {n} is not one N-Triples statement: {line[:120]!r}")
        triples.append((m.group(1), m.group(2), m.group(3)))
    return triples


def promotion_digest(triples: list[Triple], target_plane: str) -> str:
    """Stable id of WHAT is promoted WHERE, so a record names its edge set and
    re-running the same promotion cannot mint a second record."""
    h = hashlib.sha256(target_plane.encode())
    for t in sorted(set(triples)):
        h.update(("\n" + " ".join(t)).encode())
    return h.hexdigest()[:16]


def _literal(text: str) -> str:
    return json.dumps(text)  # a valid Turtle string literal, escapes included


def promotion_write(
    triples: list[Triple],
    target_plane: str,
    promoted_by: str,
    authored_by: str,
    reason: str,
    timestamp: str,
    source_left_open: bool = True,
    superseded: Triple | None = None,
) -> dict:
    """The `/knot` body that ASSERTS the promoted triples in the target plane,
    with the promotion record beside them in the same transaction.

    The record is provenance, never a stand-in for the fact: the previous
    version wrote only the record, so a promotion moved zero edges while the
    record made it look done (aegis-is257g). The record carries the promotion's
    own provenance as `observed`, because the move IS an observed event even
    though the facts it moves were inferred.
    """
    digest = promotion_digest(triples, target_plane)
    if superseded is not None:
        digest = hashlib.sha256(("supersede\0" + digest + "\0" + " ".join(superseded)).encode()).hexdigest()[:16]
    record_iri = f"<urn:camayoc:plane-promotion:{digest}>"
    facts = "\n".join(f"{s} {p} {o} ." for s, p, o in sorted(set(triples)))
    subjects = sorted({t[0] for t in triples})
    # Each promoted subject points at the record (the stored Q13 query reads
    # `<fact> camayoc:planePromotion ?record`), and the record derives from it.
    links = "\n".join(f"{s} camayoc:planePromotion {record_iri} ." for s in subjects)
    derived = " , ".join(subjects)
    record = f"""{record_iri}
    camayoc:promotedFrom  <{planes.plane_for("inferred")}> ;
    camayoc:promotedInto  <{planes.PLANES[target_plane]["iri"]}> ;
    camayoc:promotedBy    {_literal(promoted_by)} ;
    camayoc:authoredBy    {_literal(authored_by)} ;
    camayoc:promotedAt    {_literal(timestamp)} ;
    camayoc:promotedTripleCount {len(set(triples))} ;
    camayoc:promotionDigest {_literal(digest)} ;
    aegis:sourceKind      "observed" ;
    aegis:falsifier       {_literal(f"any of the {len(set(triples))} promoted triples is absent from the target plane after this transaction")} ;
    camayoc:sourceLeftOpen {"true" if source_left_open else "false"} ;
    prov:wasDerivedFrom   {derived} ;
    camayoc:reason        {_literal(reason)} .
"""
    if superseded is not None:
        record += (f"{record_iri} camayoc:supersededTriple {_literal(' '.join(superseded) + ' .')} ;\n"
                   f"    camayoc:replacementTriple {_literal(' '.join(triples[0]) + ' .')} .\n")
    turtle = (
        "@prefix camayoc: <https://camayoc.local/ontology/> .\n"
        "@prefix aegis:   <http://aegis.gastown.local/ontology/> .\n"
        "@prefix prov:    <http://www.w3.org/ns/prov#> .\n\n"
        f"{facts}\n\n{links}\n\n{record}"
    )
    return {
        "turtle": turtle,
        "graph": planes.PLANES[target_plane]["iri"],
        "actor": promoted_by,
        "source": f"camayoc plane promotion {digest}",
        "digest": digest,
    }


def ask_in_graph(graph_iri: str, triple: Triple) -> bool:
    """Is this exact triple in this graph? Raises on "could not look"."""
    s, p, o = triple
    result = planes._post(
        "/query",
        {"query": f"ASK {{ GRAPH <{graph_iri}> {{ {s} {p} {o} }} }}"},
        client="camayoc-planes",
    )
    if not isinstance(result.get("result"), bool):
        raise planes.PlaneError(f"ASK returned no boolean: {str(result)[:200]}")
    return result["result"]


def promote(
    triples: list[Triple],
    target_plane: str,
    promoted_by: str,
    authored_by: str,
    reason: str,
    timestamp: str,
    grants: dict[str, list[str]] | None = None,
    source_episode: str | None = None,
    source_has=None,
) -> tuple[dict, dict | None]:
    """Run every gate, then produce the promotion write and the source close.

    Gates run BEFORE anything is written, cheapest first. `source_has(triple)`
    answers whether a triple is in the source plane (prod: an ASK on
    crew:inferred); every promoted triple must be, or the record would assert a
    move that never happened. Returns `(write, close)`: `write` is a `/knot`
    body asserting the triples + record in the target plane; `close` is a
    `POST /episode/retract` body for `source_episode`, or None (the record then
    says `camayoc:sourceLeftOpen true`). The caller commits in the order
    assert -> verify every triple in the target -> close, so a failed assert or
    verify never closes the source (aegis-is257g: closing before the edges
    exist in the target loses them from the current view).
    """
    if not reason.strip():
        raise PromotionRefused(
            "a promotion must state its reason; an unexplained trust upgrade is "
            "exactly the record a later reader cannot assess"
        )
    if not triples:
        raise PromotionRefused("nothing to promote: the edge set is empty")
    check_outranks(target_plane)
    check_not_self_promotion(promoted_by, authored_by)
    check_authority(promoted_by, target_plane, grants if grants is not None else load_authority())
    if source_has is not None:
        missing = [t for t in sorted(set(triples)) if not source_has(t)]
        if missing:
            shown = "; ".join(" ".join(t) for t in missing[:5])
            raise PromotionRefused(
                f"{len(missing)} triple(s) are not in {SOURCE_PLANE}, so promoting them "
                f"would record a move that never happened: {shown}"
            )
    write = promotion_write(
        triples, target_plane, promoted_by, authored_by, reason, timestamp,
        source_left_open=source_episode is None,
    )
    close = None
    if source_episode is not None:
        close = {
            "episode": source_episode,
            "timestamp": timestamp,
            "actor": promoted_by,
            # Entities the promoted facts still reference must survive the
            # close: retraction of the carrier episode is an interval close,
            # not an identity purge.
            "on_orphan": "preserve",
        }
    return write, close



def supersede(
    triples: list[Triple], target_plane: str, promoted_by: str,
    authored_by: str, reason: str, timestamp: str, superseded: Triple,
    grants=None, source_has=None, target_has=None,
) -> dict:
    """One same-property compare-and-swap, audited in the SAME target graph.

    Preflight reads are diagnostic. The old target and new source facts and
    absence of another target value are rechecked by the server under its writer
    lock. No source episode is closed; no multi-graph atomicity is assumed.
    """
    if (len(triples) != 1 or triples[0][:2] != superseded[:2]
            or triples[0] == superseded):
        raise PromotionRefused("supersede requires one changed object on the same subject/predicate")
    # Reuse every authority, ranking, authorship and source-presence gate.
    promote(triples, target_plane, promoted_by, authored_by, reason, timestamp,
            grants=grants, source_has=source_has)
    if target_has is None or not target_has(superseded):
        raise PromotionRefused("the exact superseded fact is not present in the target plane")
    write = promotion_write(triples, target_plane, promoted_by, authored_by,
                            reason, timestamp, superseded=superseded)
    prefixes, facts = write.pop("turtle").split("\n\n", 1)
    prefixes = "\n".join(line.replace("@prefix ", "PREFIX ", 1).removesuffix(" .")
                         for line in prefixes.splitlines())
    graph = write["graph"]
    source = planes.PLANES[SOURCE_PLANE]["iri"]
    old = " ".join(superseded) + " ."
    new = " ".join(triples[0]) + " ."
    subject, predicate, old_object = superseded
    write["update"] = (f"{prefixes}\nDELETE {{ GRAPH <{graph}> {{ {old} }} }}\n"
        f"INSERT {{ GRAPH <{graph}> {{ {facts} }} }}\n"
        f"WHERE {{ GRAPH <{graph}> {{ {old} }} GRAPH <{source}> {{ {new} }} "
        f"FILTER NOT EXISTS {{ GRAPH <{graph}> {{ {subject} {predicate} ?other . "
        f"FILTER (!sameTerm(?other, {old_object})) }} }} }}")
    return write


def commit_supersede(write: dict, old: Triple, new: Triple) -> int:
    """Never retry an indeterminate write; verify both planes after one commit."""
    try:
        result = planes._post("/update", {k: write[k] for k in ("update", "actor", "source")},
                              client="camayoc-planes", form=True)
    except planes.PlaneError as exc:
        print(f"SUPERSEDE indeterminate: verify before retrying: {exc}", file=sys.stderr)
        return 3
    graphs = result.get("graphs") if isinstance(result, dict) else None
    if (not isinstance(result, dict) or type(result.get("tx")) is not int
            or type(result.get("retracted")) is not int or result["retracted"] != 1
            or type(result.get("asserted")) is not int or result["asserted"] < 1
            or not isinstance(graphs, list) or len(graphs) != 1
            or not isinstance(graphs[0], dict)
            or graphs[0].get("graph") != write["graph"]):
        print("SUPERSEDE not confirmed: precondition did not match or report UNKNOWN; verify before retrying",
              file=sys.stderr)
        return 3
    try:
        verified = (ask_in_graph(write["graph"], new)
                    and not ask_in_graph(write["graph"], old)
                    and ask_in_graph(planes.PLANES[SOURCE_PLANE]["iri"], new))
    except planes.PlaneError:
        verified = False
    if not verified:
        print(f"SUPERSEDE committed tx {result['tx']} but read-back UNKNOWN; no retry", file=sys.stderr)
        return 5
    print(f"superseded exactly one target fact (tx {result['tx']}, digest {write['digest']}); "
          "replacement and old absence read back; inferred source remains open")
    return 0


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--triples-file", required=True,
                    help="N-Triples file: the EXACT edges to promote (all must be in crew:inferred)")
    ap.add_argument("--to", required=True, dest="target", help="target plane key")
    ap.add_argument("--by", required=True, help="principal performing the promotion")
    ap.add_argument("--authored-by", required=True, help="principal that wrote the facts")
    ap.add_argument("--reason", required=True, help="why these have earned promotion")
    ap.add_argument("--timestamp", default=None, help="ISO-8601 UTC; default: now")
    ap.add_argument(
        "--source-episode",
        help="episode that carried the facts into crew:inferred. Its interval is CLOSED "
        "only after every promoted triple reads back in the target. The close retracts "
        "EVERYTHING that episode carried, so pass it only when the triples file is that "
        "episode's complete edge set",
    )
    ap.add_argument("--supersedes", help="N-Triples file containing exactly one old target fact; never a source episode")
    ap.add_argument("--emit", action="store_true",
                    help="run every gate (including the read-only source check), print the "
                    "write and the close, and write nothing")
    args = ap.parse_args()
    timestamp = args.timestamp or _now()
    source_iri = planes.PLANES[SOURCE_PLANE]["iri"]

    try:
        triples = parse_ntriples(Path(args.triples_file).read_text())
        if args.supersedes:
            old = parse_ntriples(Path(args.supersedes).read_text())
            if len(old) != 1 or args.source_episode:
                raise PromotionRefused("supersede needs exactly one old fact and forbids source-episode retraction")
            write = supersede(triples, args.target, args.by, args.authored_by,
                              args.reason, timestamp, old[0],
                              source_has=lambda t: ask_in_graph(source_iri, t),
                              target_has=lambda t: ask_in_graph(planes.PLANES[args.target]["iri"], t))
            if args.emit:
                print(json.dumps({"supersession": write, "close": None}, indent=2))
                return 0
            return commit_supersede(write, old[0], triples[0])
        write, close = promote(
            triples, args.target, args.by, args.authored_by, args.reason, timestamp,
            source_episode=args.source_episode,
            source_has=lambda t: ask_in_graph(source_iri, t),
        )
    except (PromotionRefused, planes.PlaneError, OSError) as exc:
        print(f"PROMOTION REFUSED: {exc}", file=sys.stderr)
        return 2

    if args.emit:
        print(json.dumps({"promotion": write, "close": close}, indent=2))
        return 0

    body = {k: v for k, v in write.items() if k != "digest"}
    try:
        result = planes._post("/knot", body, client="camayoc-planes")
    except planes.PlaneError as exc:
        print(f"PROMOTION FAILED (indeterminate: verify before retrying): {exc}", file=sys.stderr)
        return 3
    if result.get("conforms") is not True or not isinstance(result.get("tx_id"), int):
        print(f"PROMOTION REFUSED by the store: {str(result)[:300]}", file=sys.stderr)
        return 3

    target_iri = write["graph"]
    try:
        absent = [t for t in sorted(set(triples)) if not ask_in_graph(target_iri, t)]
    except planes.PlaneError as exc:
        print(f"PROMOTED (tx {result['tx_id']}) but the read-back could not run: {exc}. "
              "Source NOT closed.", file=sys.stderr)
        return 5
    if absent:
        print(f"PROMOTED (tx {result['tx_id']}) but {len(absent)} triple(s) do not read back "
              f"in {args.target}; source NOT closed: "
              + "; ".join(" ".join(t) for t in absent[:5]), file=sys.stderr)
        return 5

    n = len(set(triples))
    if close is not None:
        try:
            planes._post("/episode/retract", close, client="camayoc-planes")
        except planes.PlaneError as exc:
            # Assert landed and read back, close did not: readable in two planes,
            # at different trust - visible and recoverable, and said out loud.
            print(
                f"PROMOTED {n} triples (tx {result['tx_id']}) but source close FAILED: {exc}. "
                "They are readable in both planes; re-run the close with "
                f"POST /episode/retract {json.dumps(close)}",
                file=sys.stderr,
            )
            return 4
        print(f"promoted {n} triples into {args.target} by {args.by} (tx {result['tx_id']}, "
              f"digest {write['digest']}); all read back; source episode closed")
    else:
        print(f"promoted {n} triples into {args.target} by {args.by} (tx {result['tx_id']}, "
              f"digest {write['digest']}); all read back; source interval LEFT OPEN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
