#!/usr/bin/env python3
"""Read bounded native planned-work candidates; never write or replay a tracker."""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from datetime import datetime

import planes

MAX_ITEMS = 25
MAX_ROWS = 100
A = "http://aegis.gastown.local/ontology/"
S = "https://schema.org/"
KINDS = ("stiwi-directive", "design", "plan")
STOP = {"a", "an", "the", "for", "of", "to", "and", "in", "what", "which"}


def terms(question):
    words = list(dict.fromkeys(re.findall(r"[a-z0-9]+", question.lower())))
    words = [word for word in words if word not in STOP]
    if not 1 <= len(words) <= 3 or len(question) > 256:
        raise ValueError("use one to three retrieval terms; no empty board listing")
    return words


def iri(value):
    if not isinstance(value, str) or not re.fullmatch(r"https?://[^\s<>\"{}\\]+", value):
        raise ValueError("explicit absolute graph IRI required")
    return value


def candidates(response, words):
    """Keep native fields only; conflicts and overflow are UNKNOWN, never empty."""
    if not isinstance(response, dict):
        raise ValueError("invalid native response")
    rows = response.get("rows")
    if (not isinstance(rows, list) or response.get("truncated")
            or len(rows) > MAX_ROWS):
        raise ValueError("native candidate response incomplete")
    groups = defaultdict(list)
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("invalid native row")
        groups[row.get("item")].append(row)
    if len(groups) > MAX_ITEMS:
        raise ValueError("native candidate budget exceeded; narrow the question")
    result = []
    fields = ("id", "status", "title", "description", "owner", "modified", "type")
    for item, records in groups.items():
        iri(item)
        values = {key: {json.dumps(row.get(key), sort_keys=True) for row in records}
                  for key in fields}
        if any(len(value) != 1 for value in values.values()):
            raise ValueError("conflicting native fields")
        record = {key: records[0].get(key) for key in fields}
        labels = {row.get("label") for row in records}
        if (not isinstance(record["id"], str)
                or not re.fullmatch(r"[A-Za-z0-9_.-]+", record["id"])
                or record["status"] not in {"open", "in_progress", "blocked", "deferred", "closed"}
                or record["type"] not in {S + "Action", "schema:Action"}
                or not labels <= set(KINDS) or not labels
                or not isinstance(record["title"], str)
                or record["description"] is not None and not isinstance(record["description"], str)):
            raise ValueError("invalid native planned action")
        if record["owner"] is not None:
            iri(record["owner"])
        modified = record["modified"]
        if isinstance(modified, dict) and modified.get("datatype") in {
                "xsd:dateTime", "http://www.w3.org/2001/XMLSchema#dateTime"}:
            modified = modified.get("value")
        if not isinstance(modified, str) or not re.fullmatch(
                r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,9})?Z", modified):
            raise ValueError("unproven native modification timestamp")
        datetime.fromisoformat(re.sub(r"(\.\d{6})\d+", r"\1", modified[:-1]) + "+00:00")
        record["modified"] = modified
        if record["status"] == "closed":
            continue
        text = (record["title"] + " " + (record["description"] or "")).lower()
        matches = [word for word in words if word in text]
        if not matches:
            continue  # Retrieval proposes candidates; native text determines lexical evidence.
        kind = next(k for k in KINDS if k in labels)
        result.append({**record, "item": item, "kind": {
            "stiwi-directive": "Directive", "design": "Design", "plan": "Plan"}[kind],
                       "matched_terms": matches, "about": [], "authority": "native-board"})
    return sorted(result, key=lambda r: (-len(r["matched_terms"]), r["id"]))


def read(question, board, records, post, identifiers):
    words = terms(question)
    board, records = iri(board), iri(records)
    control = post("/query", {"query": f"SELECT ?item FROM <{board}> WHERE {{ "
                   f"?item a <{S}Action> }} LIMIT 1"})
    if not isinstance(control, dict) or not isinstance(control.get("rows"), list) or not control["rows"]:
        raise ValueError("native board visibility control unproven")
    if (not isinstance(identifiers, list) or not 1 <= len(identifiers) <= MAX_ITEMS
            or len(set(identifiers)) != len(identifiers)
            or any(not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", value)
                   for value in identifiers)):
        raise ValueError("one to25 distinct exact candidate identifiers required")
    raw = []
    for identifier in identifiers:
        response = post("/ask", {"name": "camayoc_planned_actions",
                                 "params": {"board": board, "identifier": identifier}})
        if not isinstance(response, dict):
            raise ValueError("invalid native response")
        rows = response.get("rows")
        if (not isinstance(rows, list) or response.get("truncated") or len(rows) > MAX_ROWS
                or any(not isinstance(row, dict) or row.get("id") != identifier for row in rows)):
            raise ValueError("exact native candidate response unproven")
        raw.extend(rows)
    found = candidates({"rows": raw}, words)
    if not found:
        return {"status": "OK", "items": [], "scope": "bounded lexical candidates"}
    # One bounded exact-ID enrichment read. Never select legacy status/owner/date.
    identifiers = " ".join(json.dumps(row["id"]) for row in found)
    query = (f"SELECT DISTINCT ?id ?about FROM <{records}> WHERE {{ "
             f"VALUES ?id {{ {identifiers} }} ?legacy <{A}identifier> ?id ; "
             f"<{A}about> ?about . }} LIMIT 101")
    enrichment = post("/query", {"query": query})
    if not isinstance(enrichment, dict):
        raise ValueError("invalid topic enrichment response")
    links = enrichment.get("rows")
    if not isinstance(links, list) or enrichment.get("truncated") or len(links) > MAX_ROWS:
        raise ValueError("exact-ID topic enrichment incomplete")
    by_id = {row["id"]: row for row in found}
    for link in links:
        if not isinstance(link, dict) or link.get("id") not in by_id:
            raise ValueError("unscoped topic enrichment")
        target = link.get("about")
        if isinstance(target, str) and target.startswith("aegis:"):
            target = A + target.removeprefix("aegis:")
        target = iri(target)
        row = by_id[link["id"]]
        if target not in row["about"]:
            row["about"].append(target)
    return {"status": "OK", "items": found, "scope": "bounded lexical candidates",
            "warning": "Ranking is lexical evidence, not semantic completeness or authentication."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question")
    parser.add_argument("--board", required=True)
    parser.add_argument("--records", required=True)
    parser.add_argument("--id", dest="identifiers", action="append", required=True,
                        help="Exact candidate ID from bounded semantic retrieval or a seed creation event")
    args = parser.parse_args()
    try:
        result = read(args.question, args.board, args.records,
                      lambda path, body: planes._post(path, body, client="agent-adhoc"), args.identifiers)
    except (ValueError, planes.PlaneError, TypeError, KeyError):
        print(json.dumps({"status": "UNKNOWN", "items": None}))
        return 2
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
