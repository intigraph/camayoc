#!/usr/bin/env python3
"""Suggest the quipu entity a work item is about: search retrieves, Jev picks.

aegis-4hhqoe.12. Jev cannot search, and search cannot decide, so this is two
steps:

1. RETRIEVE. quipu `/search` on the item's title and the head of its
   description, top-k. Provenance and text nodes are dropped: an `episode_*`
   node is the record of a write, and code chunks and doc sections are passages.
   A work item is about none of those.
2. DECIDE. ONE Jev `choice` over the candidates plus none-of-these. The
   none-of-these option IS the abstention, so there is no numeric floor here.
   That is deliberate: the bead asks to share the bobbin retrieval floor's
   calibration (aegis-4hhqoe.3) rather than invent a second threshold, and that
   calibration does not exist yet. A per-candidate noul against .3's floor can
   replace this once it lands.

`--write` records the accepted link as `<item> aegis:about <entity>` with
`sourceKind inferred`, routed by `planes.plane_for('inferred')` into the
quarantine plane. Nothing here promotes it: a Jev pick is an inference, and the
epic's rule is that Jev never promotes its own output.

Every proposal names an entity that exists, because candidates come from the
store. Absence of a proposal is "none of these fit", never "no such entity".
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jev  # noqa: E402

SERVER = os.environ.get("QUIPU_SERVER", "http://localhost:3030").rstrip("/")
ONTOLOGY = "http://aegis.gastown.local/ontology/"
WORKITEM_NS = ONTOLOGY + "workitem/"
CLIENT = "camayoc-entity-link"
TOP_K = 20
#: One fixed retry, then the item is an ERROR, counted apart from "none"
#: (gennaro's J4 rule: a failed call must never read as an abstention).
RETRIES = 1
#: Lifecycle and ledger nodes ABOUT a bead or a write, not things work acts on:
#: the bead's own close event is circular, a directive-ledger row is a record.
NOISE = re.compile(r"(^|[:/])(bead-(closed|created)-|ledger-[0-9a-f]{6,}-|"
                   r"reactor-(closed|created)-)|/commit/")
DESCRIPTION_HEAD = 400
#: Names the three near-miss classes an independent blind rating found in ALL 7
#: of its rejections (13/20 at the first instruction): a parent or umbrella
#: entity, a sibling or predecessor issue, and an instrument used in the work.
#: None of those rejections was unrelated, so this is a ranking instruction,
#: not a retrieval change (aegis-4hhqoe.12).
INSTRUCTIONS = (
    "The state is one work item (a tracker bead). Pick the knowledge-graph "
    "entity this work item ACTS ON: the most specific thing it builds, fixes, "
    "measures or changes. Do NOT pick a neighbour of that subject: not its "
    "parent, umbrella or containing component (e.g. a whole service or a "
    "stack-wide standard when the work targets one part of it); not a sibling, "
    "predecessor or earlier related issue; not a tool, record, snapshot or "
    "observation used or produced along the way. If every option is only such "
    "a neighbour, pick none-of-these."
)
NONE_TEXT = "None of these is the subject of this work item"


def _post(path: str, body: dict, token: str = "") -> dict:
    from planes import provenance_headers

    headers = {"Content-Type": "application/json", "X-Quipu-Client": CLIENT,
               **provenance_headers(CLIENT)}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(SERVER + path, json.dumps(body).encode(), headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def is_entity(iri: str) -> bool:
    """A thing a work item can be about: not provenance, not a passage."""
    local = iri.split("/")[-1].split(":")[-1]
    if local.startswith("episode_") or NOISE.search(iri):
        return False
    return not any(part in iri for part in ("/code/", "/doc/", "/chunk/", "#"))


def _entities(raw: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for hit in raw.get("results", []):
        iri = hit.get("entity") or ""
        if iri and iri not in out and is_entity(iri):
            out[iri] = (hit.get("text") or "")[:300]
    return out


def candidates(title: str, description: str, search=None, item: str = "") -> list[dict]:
    """Up to TOP_K distinct entities: title-only and title+description results
    interleaved. Measured on 89 labelled beads, the interleave held the label
    in its pool as often as either query alone at k=10 and more often at k=40;
    a title-only query alone was worst (30% at k=10)."""
    search = search or (lambda q: _post("/search", {"query": q, "limit": TOP_K * 3}))
    full = f"{title}\n{(description or '')[:DESCRIPTION_HEAD]}".strip()
    a, b = _entities(search(title)), _entities(search(full))
    order: list[str] = []
    for pair in zip(list(a) + [None] * TOP_K * 3, list(b) + [None] * TOP_K * 3):
        for iri in pair:
            if iri and iri not in order:
                order.append(iri)
    texts = {**b, **a}
    # The item's OWN node is never a candidate: "this bead is about itself"
    # was picked for 8 of 89 beads in the first evaluation, and it hands the
    # resuming agent nothing.
    order = [iri for iri in order if not item or _local(iri) != item]
    return [{"entity": iri, "text": texts[iri]} for iri in order[:TOP_K]]


def _local(iri: str) -> str:
    if iri.startswith("aegis:"):
        return iri.split(":", 1)[1]
    return iri.rsplit("/", 1)[-1]


def link(title: str, description: str, client=None, search=None, item: str = "") -> dict:
    """{'candidates', 'choice' (an entity or None), 'confidence', 'usage'}."""
    cands = candidates(title, description, search, item)
    result = {"candidates": cands, "choice": None, "confidence": None, "usage": {},
              "error": None}
    if not cands:
        result["reason"] = "search returned no entity candidates"
        return result
    # Option keys are the entity IRIs themselves, so the answer needs no lookup
    # table and cannot name anything that was not offered.
    criteria = {c["entity"]: c["text"] or c["entity"] for c in cands}
    client = client or jev.JevClient()
    state = {"title": title, "description": (description or "")[:2000]}
    for attempt in range(RETRIES + 1):
        try:
            out = client.choice(state, INSTRUCTIONS, criteria, none_text=NONE_TEXT)
            break
        except (jev.JevUnavailable, TimeoutError, OSError) as exc:
            if attempt == RETRIES:
                result["error"] = f"{type(exc).__name__}: {exc}"[:200]
                return result
    pick = out.get("choice")
    result["confidence"] = out.get("confidence")
    result["usage"] = out.get("usage", {})
    if pick and pick != jev.NONE_OPTION and pick in criteria:
        result["choice"] = pick
    return result


def bead(item: str, db: str) -> tuple[str, str]:
    raw = subprocess.run(["br", "--db", db, "show", item, "--format", "json"],
                         check=True, capture_output=True, text=True).stdout
    row = json.loads(raw)[0]
    return row.get("title") or "", row.get("description") or ""


def write_link(item: str, entity: str, token: str, timestamp: str) -> dict:
    """`<item> aegis:about <entity>`, sourceKind inferred, in the quarantine plane.

    The plane is registered only when quipu says it is not. Registering on
    every write re-POSTed /graph/create and /graph/label for every plane on
    each dispatch that named no node, which is load on a single writer for
    nothing once the planes exist (review note on aegis-4hhqoe.12).
    """
    import urllib.error

    import planes
    subject = f"<{WORKITEM_NS}{item}>"
    turtle = (f"{subject} <{ONTOLOGY}about> <{_full(entity)}> .\n"
              f"{subject} <{ONTOLOGY}sourceKind> \"inferred\" .\n")
    body = {"turtle": turtle, "graph": planes.plane_for("inferred")}
    try:
        return _post("/knot", body, token)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        if "unknown graph" not in detail:
            raise
    planes.ensure_planes(timestamp)
    return _post("/knot", body, token)


def _full(iri: str) -> str:
    return ONTOLOGY + iri.split(":", 1)[1] if iri.startswith("aegis:") else iri


def work_items(value) -> list[dict]:
    """Validate all caller-resolved text before any retrieval or inferred write."""
    if (not isinstance(value, dict) or set(value) != {"version", "items"}
            or type(value["version"]) is not int or value["version"] != 1):
        raise ValueError("expected version 1 work-item envelope")
    rows = value["items"]
    if not isinstance(rows, list) or not rows:
        raise ValueError("expected a nonempty items list")
    seen = set()
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {"id", "title", "description"}
                or not isinstance(row["id"], str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", row["id"])
                or row["id"] in seen
                or not isinstance(row["title"], str) or not row["title"].strip()
                or not isinstance(row["description"], str)):
            raise ValueError("item identity or text is invalid, incomplete or duplicated")
        seen.add(row["id"])
    return rows


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("items", nargs="*", help="bead ids (legacy br reader)")
    p.add_argument("--work-item-json", metavar="PATH",
                   help="versioned authoritative work-item JSON; '-' reads stdin, never br")
    p.add_argument("--db", default=os.environ.get(
        "BR_DB", str(Path.home() / "gt/beads_aegis/mayor/rig/_beads/beads.db")))
    p.add_argument("--write", action="store_true",
                   help="record accepted links in the inferred (quarantine) plane")
    p.add_argument("--timestamp", help="ISO-8601 instant for plane registration")
    a = p.parse_args(argv)
    supplied = None
    if a.work_item_json:
        tokens = argv if argv is not None else sys.argv[1:]
        if a.items or any(arg == "--db" or arg.startswith("--db=") for arg in tokens):
            p.error("--work-item-json cannot be combined with bead IDs or --db")
        try:
            raw = sys.stdin.read() if a.work_item_json == "-" else Path(a.work_item_json).read_text()
            supplied = work_items(json.loads(raw))
        except (OSError, ValueError) as exc:
            p.error(f"invalid authoritative work-item input: {exc}")
    elif not a.items:
        p.error("provide bead IDs or --work-item-json")
    token = os.environ.get("QUIPU_AUTH_TOKEN", "")
    if a.write and not (token and a.timestamp):
        p.error("--write needs QUIPU_AUTH_TOKEN and --timestamp")
    client = jev.JevClient()
    rows = supplied if supplied is not None else (
        {"id": item, "title": title, "description": description}
        for item in a.items for title, description in [bead(item, a.db)])
    for row in rows:
        item, title, description = row["id"], row["title"], row["description"]
        res = {"item": item, **link(title, description, client, item=item)}
        if a.write and res["choice"]:
            res["write"] = write_link(item, res["choice"], token, a.timestamp)
        print(json.dumps(res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
