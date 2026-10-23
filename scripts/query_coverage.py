#!/usr/bin/env python3
"""Per-question stored-query coverage for the competency suite — camayoc-102.

The competency suite sets its own rule: *every question must eventually run as
a named stored query* (quipu #79). This reports how far from that we are, per
question, and treats the shortfall as a result rather than a chore.

Three levels, deliberately kept apart, because they fail for different reasons
and collapsing them is the defect this repo keeps finding elsewhere:

    EXPRESSIBLE   the ontology carries the terms the question needs.
                  When it does not, that is a COMPETENCY GAP — the finding
                  camayoc-b6h describes, not a missing chore.
    STORED        a named stored query exists in queries/.
    EXECUTED      the query runs against the fixture graph and returns rows
                  a human agreed with (asserted in tests/test_competency_queries.py).

A question can be EXPRESSIBLE and not STORED (work to do), but never STORED
without being EXPRESSIBLE — which the self-check below enforces, because a
stored query naming a predicate the ontology does not define would return zero
rows forever and read as "nothing to report".

Those levels reach the OUTPUT as three states. Until 2026-08-25 they reached
it as two, which was this module collapsing the distinction its own docstring
draws:

    STORED      a named query answers the question.
    UNWRITTEN   expressible, and nobody wrote it. Work with a known shape.
    GAP         the ontology cannot express it. A competency gap, reported as
                itself.

Only STORED counts toward coverage. An unwritten query is nearer to done than
a competency gap and that is exactly why it must not be counted: a slice whose
questions are all expressible and none stored answers nothing.

EVERY SLICE IS COUNTED, AND A TEST HOLDS IT THERE. The tables live in
scripts/coverage_tables.py and tests/test_coverage_slices.py asserts they
match competency/*.md exactly. This module twice reported a coverage figure
over a denominator that omitted real questions — §D in 2026-08-22, four whole
slices in 2026-08-25 — and an uncounted question is a gap unreported.

Usage:
    python3 scripts/query_coverage.py            # the report
    python3 scripts/query_coverage.py --json     # machine-readable
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QUERIES = ROOT / "queries"
ONTOLOGY = ROOT / "ontology" / "core.ttl"

#: The tables themselves live in scripts/coverage_tables.py — data, and a
#: judgment only a human can make. Loaded rather than imported so this stays a
#: standalone script with no package on the path.
def _load_tables():
    spec = importlib.util.spec_from_file_location(
        "coverage_tables", Path(__file__).resolve().parent / "coverage_tables.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


SLICES: dict[str, dict[int, dict]] = _load_tables().SLICES


#: The namespaces the grounding self-check knows how to check. A prefix that
#: is NOT listed here is not checked at all, which is a hole rather than a
#: pass — so anything camayoc's own queries write in must be here.
GROUNDED_PREFIXES = ("aegis", "camayoc")


def ontology_terms() -> dict[str, set[str]]:
    """Local names the ontology declares or records, per namespace prefix.

    TWO WAYS A TERM COUNTS AS GROUNDED, and they are not the same thing:

      * DECLARED — `aegis:foo a rdf:Property` / `rdfs:Class`. camayoc minted
        it, owed to a competency question.
      * RECORDED AS REUSED — `aegis:foo rdfs:isDefinedBy <owner>`, with no
        minting declaration. The term is quipu's (or another owner's) and
        camayoc reuses it. Recording the reuse is what distinguishes it from
        a typo, which is the only other thing an undeclared term can be.

    The second case exists because this check used to punish the repo's own
    reuse-before-minting rule: a stored query correctly reusing
    `aegis:VerifierRegistration` was reported UNGROUNDED, and the only ways
    out were to re-mint quipu's term in quipu's namespace or to not write the
    query. Neither is right. The reuse block in ontology/core.ttl is.
    """
    text = ONTOLOGY.read_text()
    terms: dict[str, set[str]] = {p: set() for p in GROUNDED_PREFIXES}
    for prefix in GROUNDED_PREFIXES:
        declared = re.findall(
            rf"^{prefix}:(\w+)\s+a\s+(?:rdf:Property|rdfs:Class)", text, re.M
        )
        reused = re.findall(rf"^{prefix}:(\w+)\s*\n\s+rdfs:isDefinedBy", text, re.M)
        terms[prefix] = set(declared) | set(reused)
    return terms


def query_predicates(template: str) -> set[tuple[str, str]]:
    """The (prefix, local name) pairs a stored query's template references.

    Namespace-qualified rather than a bare set of local names: `camayoc:foo`
    and `aegis:foo` are different terms, and checking one against the other's
    declarations would call an undefined term grounded.
    """
    pattern = "|".join(GROUNDED_PREFIXES)
    # A QName cannot start inside another colon-delimited IRI token.
    # In particular urn:camayoc:workitem-... does not reference camayoc:work.
    return set(re.findall(rf"(?<![\w:])({pattern}):(\w+)", template))


def load_queries() -> dict[str, dict]:
    return {p.stem: json.loads(p.read_text()) for p in sorted(QUERIES.glob("*.json"))}


def report(slice_name: str = "verification-and-liveness") -> dict:
    terms = ontology_terms()
    stored = load_queries()
    COVERAGE = SLICES[slice_name]

    rows = []
    for number in sorted(COVERAGE):
        entry = COVERAGE[number]
        name = entry.get("query")
        row = {"question": number, "query": name}

        if name is None and "expressible" in entry:
            # EXPRESSIBLE but not STORED. Kept apart from GAP because they are
            # different findings with different owners: this is a query nobody
            # has written, not vocabulary the ontology lacks. Reporting both as
            # "gap" makes unwritten work look like a modelling problem, which
            # is the more forgivable of the two and the wrong answer.
            row["state"] = "UNWRITTEN"
            row["gap"] = entry["expressible"]
            row["needs"] = []
        elif name is None:
            row["state"] = "GAP"
            row["gap"] = entry["gap"]
            row["needs"] = entry.get("needs", [])
        elif name not in stored:
            # The table claims a query that is not on disk. Loud, not silent.
            row["state"] = "MISSING"
            row["gap"] = f"{name}.json is named here but absent from queries/"
        else:
            undefined = sorted(
                f"{prefix}:{local}"
                for prefix, local in query_predicates(stored[name]["template"])
                if local not in terms[prefix]
            )
            if undefined:
                row["state"] = "UNGROUNDED"
                row["gap"] = (
                    f"references predicates the ontology does not define: {undefined}. "
                    "It would return zero rows forever and read as 'nothing to report'."
                )
            else:
                row["state"] = "STORED"
        rows.append(row)

    covered = sum(1 for r in rows if r["state"] == "STORED")
    total = len(rows)
    verdict = "Full" if covered == total else ("Empty" if covered == 0 else "Partial")

    return {
        "slice": slice_name,
        "covered": covered,
        "total": total,
        "verdict": verdict,
        # Reported separately and never folded into `covered`. An unwritten
        # query is closer to done than a competency gap, and that is exactly
        # why it must not be counted as coverage: a slice whose questions are
        # all expressible and none stored answers nothing.
        "unwritten": sum(1 for r in rows if r["state"] == "UNWRITTEN"),
        "gaps": sum(1 for r in rows if r["state"] == "GAP"),
        "rows": rows,
        "stored_query_count": len(stored),
    }


def totals() -> dict:
    """The whole-suite figure. There was no such thing before 2026-08-25,
    because there was no denominator: four of six slices were uncounted."""
    results = [report(name) for name in SLICES]
    return {
        "slices": len(results),
        "covered": sum(r["covered"] for r in results),
        "total": sum(r["total"] for r in results),
        "unwritten": sum(r["unwritten"] for r in results),
        "gaps": sum(r["gaps"] for r in results),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    results = [report(name) for name in SLICES]
    if args.json:
        print(json.dumps({"slices": results, "totals": totals()}, indent=2))
        return 0

    for result in results:
        print_slice(result)
    print_totals(totals())
    return 0


def print_slice(result: dict) -> None:
    print(f"competency slice: {result['slice']}")
    print(
        f"stored-query coverage: {result['covered']}/{result['total']} "
        f"— {result['verdict']} "
        f"({result['unwritten']} unwritten, {result['gaps']} competency gap(s))"
    )
    print()
    for row in result["rows"]:
        if row["state"] == "STORED":
            print(f"  Q{row['question']:<3} STORED      {row['query']}")
        else:
            print(f"  Q{row['question']:<3} {row['state']:<11} {row['gap']}")
            for need in row.get("needs", []):
                print(f"           needs: {need}")
    print()


def print_totals(figures: dict) -> None:
    print(
        f"SUITE TOTAL: {figures['covered']}/{figures['total']} stored across "
        f"{figures['slices']} slices — {figures['unwritten']} expressible and "
        f"unwritten, {figures['gaps']} competency gap(s)."
    )
    print(
        "A question with no stored query is an ontology gap reported as itself, "
        "never answered from the nearest term — and an UNCOUNTED question is a "
        "gap unreported, which is why every slice has a table and a test says so."
    )


if __name__ == "__main__":
    sys.exit(main())
