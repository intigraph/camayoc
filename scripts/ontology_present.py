#!/usr/bin/env python3
"""Is every term an ontology file declares already typed in the store?

    ontology_present.py <core.ttl> <server-url>

Exit 0: all declared terms are present (prints "N/N").
Exit 1: some are missing (prints "M/N").
Exit 2: CANNOT TELL -- the file declared nothing, or the store did not answer.
        Never read 2 as either of the others.

Why this exists: bootstrap must be re-runnable (tambo's resume re-runs it as
its functional verification). Quipu's vocabulary gate is inactive until shapes
are loaded, so the FIRST run's ontology load succeeds; bootstrap then loads
shapes, and every later load of the same file is refused with HTTP 400
"unknown rdf:type IRIs: rdf:Property, rdfs:Class" -- no loaded shape sanctions
the schema meta-classes. The refusal says nothing about whether the ontology is
already there, so bootstrap asks the store directly instead of guessing from
the error text.
"""
import json
import re
import sys
import urllib.request

DECL = re.compile(r"^([A-Za-z_][\w-]*):([\w-]+)\s+a\s+", re.M)
PREFIX = re.compile(r"^@prefix\s+([\w-]*):\s*<([^>]+)>\s*\.", re.M)


def declared_iris(text):
    prefixes = dict(PREFIX.findall(text))
    iris = []
    for pfx, local in DECL.findall(text):
        if pfx in prefixes:
            iris.append(prefixes[pfx] + local)
    return sorted(set(iris))


def count_present(server, iris):
    # COUNT, not a row comparison: quipu returns PREFIXED names ("aegis:WorkItem")
    # for subjects, so comparing rows against full IRIs reports present terms as
    # missing. A count carries no term formatting at all.
    values = " ".join(f"<{i}>" for i in iris)
    q = f"SELECT (COUNT(DISTINCT ?s) AS ?n) WHERE {{ VALUES ?s {{ {values} }} ?s a ?t }}"
    req = urllib.request.Request(
        server.rstrip("/") + "/query",
        data=json.dumps({"query": q}).encode(),
        headers={"Content-Type": "application/json", "X-Quipu-Client": "camayoc-bootstrap"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        rows = json.load(resp).get("rows")
    if not rows:
        raise ValueError("no rows in /query response")
    m = re.search(r"\d+", str(rows[0].get("n", "")))
    if not m:
        raise ValueError(f"unreadable count {rows[0]!r}")
    return int(m.group())


def main(argv):
    if len(argv) != 3:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8") as f:
        iris = declared_iris(f.read())
    if not iris:
        print("0 declared terms found -- CANNOT TELL")
        return 2
    try:
        n = count_present(argv[2], iris)
    except Exception as exc:  # any transport or shape failure is CANNOT TELL
        print(f"store did not answer ({exc}) -- CANNOT TELL")
        return 2
    print(f"{n}/{len(iris)}")
    return 0 if n == len(iris) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
