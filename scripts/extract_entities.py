#!/usr/bin/env python3
"""Quarantined entity-mention extraction from prose — deterministic-first.

Gap G4 of the txtai comparison (tambo `docs/design/txtai-gap-analysis.md`):
txtai ships NER pipelines; camayoc's ingress rule 4 says inference is
quarantined, not banned. This producer fills the slot WITHOUT a model in the
loop: every mention it emits is reproducible from its inputs, and the whole
output is still tagged `inferred` and routed to the quarantine plane — because
"this prose mentions that entity" is a reading of the text, not a record of an
event, however deterministic the reader.

TWO MATCHERS, BOTH EXPLICIT
===========================

(a) A gazetteer built from the graph's own labels (`rdfs:label`,
    `skos:prefLabel`, `skos:altLabel` via SPARQL). The graph names the things
    it knows; this script only finds those names in prose.
(b) User-supplied regex patterns from a JSON config, each mapped to an entity
    IRI somebody declared. No pattern is ever invented here.

ABSTAIN, NEVER GUESS — the same posture `ingest_git_provenance.py` had to
learn by measurement. A gazetteer label naming TWO entities matches neither:
picking one would silently attach prose to the wrong referent, and a wrong
mention is worse than a missing one even in the low-trust plane. Labels
shorter than MIN_LABEL_CHARS are skipped (a one-letter label matches
everything), matching is whole-word and case-sensitive (case-folding is a
guess this producer does not make), and fenced code blocks are skipped —
`quipu-abc` in a code sample is code, not prose. Everything skipped is
counted on stderr, because a silent abstention is indistinguishable from
coverage.

DETERMINISTIC RE-RUNS. Mention IRIs are content-addressed
(file|line|col|entity|text) and the emitted Turtle is sorted, so the same
inputs produce byte-identical output — quipu's fact log is idempotent on
identical assertions, and a re-run must not mint a second population.

QUARANTINED, ENFORCED. Output routes through `planes.plane_for("inferred")` —
never ROOT, never an observed plane — and the write refuses unless the store
lists that plane registered AND labelled (`scripts/planes.py ensure`). An
unreachable store refuses loudly: could not look is not zero mentions.

NO NEW ONTOLOGY TERMS. A mention is a generic fact over existing predicates
(`aegis:about`, `aegis:identifier`, `aegis:filePath`, `aegis:sourceKind`,
`rdfs:label`) — see `docs/design/entity-mentions.md`.

Usage:
    python3 scripts/extract_entities.py docs/*.md --dry-run
    python3 scripts/extract_entities.py docs/*.md --patterns config/x.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

import planes

#: Entity IRIs and mention predicates live under the shared aegis ontology
#: base — the SAME base `ingest_git_provenance.py` mints under, so a mention
#: of a module and the module are one node, not two populations. A parameter,
#: never a hardcoded hostname (CLAUDE.md), with the interoperable default.
ONTOLOGY = os.environ.get(
    "CAMAYOC_ONTOLOGY_NS", "http://aegis.gastown.local/ontology/"
)

#: A label this short matches everything; skipping it is reported, not silent.
MIN_LABEL_CHARS = 3

#: The graph's own names for its own things. ORDER BY makes the gazetteer —
#: and therefore ambiguity detection — independent of store iteration order.
GAZETTEER_QUERY = """\
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
SELECT ?entity ?label WHERE {
  VALUES ?p { rdfs:label skos:prefLabel skos:altLabel }
  ?entity ?p ?label .
  FILTER(isIRI(?entity))
} ORDER BY ?entity ?label"""

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_WORD = re.compile(r"\w", re.UNICODE)


class ExtractError(RuntimeError):
    """Extraction refused. Never a silent empty output — 'could not look'
    must stay distinguishable from 'looked and found nothing'."""


# ── HTTP, planes.py's client pattern verbatim ───────────────────────────────
def _call(path: str, payload: dict | None = None) -> dict:
    """GET (no payload) or POST against the quipu the planes route to.

    `planes.SERVER` / `planes.auth_token()` are read at call time so the tests (and a
    caller) can point everything at one place; splitting the config would let
    the gazetteer and the write disagree about which store is in play.
    """
    req = urllib.request.Request(
        f"{planes.SERVER}{path}",
        data=None if payload is None else json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="GET" if payload is None else "POST",
    )
    is_write = path == "/knot"
    if is_write:
        try:
            headers = planes.quipu_auth.write_headers(planes.SERVER, planes.AUTH)
        except planes.quipu_auth.CredentialRefused as exc:
            raise ExtractError(str(exc)) from None
        req.add_header("Authorization", headers["Authorization"])
    else:
        try:
            token = planes.auth_token()
        except planes.PlaneError:
            token = None
        if token:
            req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        if exc.code == 401 and is_write:
            exc.close()
            raise ExtractError(str(planes.quipu_auth.refuse(planes.SERVER, "rejected (HTTP 401)"))) from None
        if exc.code == 404:
            raise ExtractError(
                f"{path} returned 404 — this quipu predates the surface this "
                "producer needs. That is 'cannot tell', not 'nothing there'."
            ) from exc
        raise ExtractError(f"{path} failed: HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise ExtractError(
            f"{path} unreachable: {exc} — could not look is not zero mentions"
        ) from exc
    except ValueError as exc:
        raise ExtractError(f"{path} returned a body that is not JSON: {exc}") from exc


def _cell(value) -> str:
    """A SPARQL row cell as its lexical value. Lang/typed literals arrive as
    objects (quipu serves the tag rather than dropping it); the tag itself is
    not label text."""
    if isinstance(value, dict) and "value" in value:
        return str(value["value"])
    return str(value)


# ── the gazetteer ───────────────────────────────────────────────────────────
def fetch_gazetteer() -> tuple[dict[str, str], dict]:
    """label -> entity IRI, for exactly the labels safe to match on.

    Returns the usable map plus the abstention counts, because a gazetteer
    that silently dropped half its labels would make thin coverage look like
    clean prose.
    """
    # Mention triples must carry absolute entity IRIs. Quipu's compact default
    # returns CURIEs for known namespaces, so request expanded row values here.
    answer = _call("/query", {"query": GAZETTEER_QUERY, "verbose": True})
    rows = answer.get("rows") if isinstance(answer, dict) else None
    if not isinstance(rows, list):
        raise ExtractError(
            "/query returned no rows list "
            f"(keys: {sorted(answer) if isinstance(answer, dict) else answer!r}) "
            "— a gazetteer that could not be read is not an empty gazetteer."
        )
    candidates: dict[str, set[str]] = {}
    skipped_short = 0
    for row in rows:
        if not isinstance(row, dict) or "entity" not in row or "label" not in row:
            continue
        label = _cell(row["label"]).strip()
        entity = _cell(row["entity"])
        if len(label) < MIN_LABEL_CHARS or not _WORD.search(label):
            skipped_short += 1
            continue
        candidates.setdefault(label, set()).add(entity)
    gazetteer = {
        label: next(iter(entities))
        for label, entities in candidates.items()
        if len(entities) == 1
    }
    ambiguous = sorted(label for label, e in candidates.items() if len(e) > 1)
    return gazetteer, {
        "labels_usable": len(gazetteer),
        "labels_skipped_short": skipped_short,
        "labels_ambiguous": ambiguous,
    }


# ── explicit user patterns ──────────────────────────────────────────────────
def load_patterns(path: Path) -> list[tuple[re.Pattern, str]]:
    """Compile the user's declared patterns, refusing a config that cannot
    mean what it says — a pattern silently dropped would look exactly like
    prose containing no matches."""
    try:
        data = json.loads(path.read_text())
    except OSError as exc:
        raise ExtractError(f"patterns file {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ExtractError(f"patterns file {path} is not JSON: {exc}") from exc
    entries = data.get("patterns") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise ExtractError(
            f"patterns file {path} must be {{\"patterns\": [...]}} — refusing "
            "to treat an unrecognised shape as an empty pattern set"
        )
    compiled = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict) or "pattern" not in entry or "entity" not in entry:
            raise ExtractError(
                f"patterns[{i}] must carry 'pattern' and 'entity' — an entry "
                "with no entity IRI has nothing to assert a mention OF"
            )
        entity = str(entry["entity"])
        if ":" not in entity:
            raise ExtractError(f"patterns[{i}].entity {entity!r} is not an IRI")
        try:
            compiled.append((re.compile(str(entry["pattern"])), entity))
        except re.error as exc:
            raise ExtractError(f"patterns[{i}].pattern does not compile: {exc}") from exc
    return compiled


# ── scanning ────────────────────────────────────────────────────────────────
def _label_regex(label: str) -> re.Pattern:
    # Whole-word via lookarounds rather than \b: a label may begin or end on
    # a non-word character ("C++"), where \b would anchor to nothing.
    return re.compile(rf"(?<!\w){re.escape(label)}(?!\w)")


def scan_file(
    path: Path,
    gazetteer: dict[str, str],
    patterns: list[tuple[re.Pattern, str]],
) -> list[dict]:
    """Every mention in one markdown file, in deterministic order.

    Prose only: fenced code blocks are skipped, and the current ATX heading is
    carried as the mention's section so a reader can find the sentence again.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ExtractError(f"cannot read {path}: {exc}") from exc
    mentions = []
    section = "(preamble)"
    in_fence = False
    matchers = [(_label_regex(label), entity, label) for label, entity in sorted(gazetteer.items())]
    for lineno, line in enumerate(text.splitlines(), start=1):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        heading = _HEADING.match(line)
        if heading:
            section = heading.group(2) or "(untitled)"
        for regex, entity, _label in matchers:
            for match in regex.finditer(line):
                mentions.append(_mention(path, lineno, match.start(), section, match.group(0), entity))
        for regex, entity in patterns:
            for match in regex.finditer(line):
                mentions.append(_mention(path, lineno, match.start(), section, match.group(0), entity))
    mentions.sort(key=lambda m: (m["line"], m["col"], m["entity"], m["text"]))
    return mentions


def _mention(path: Path, lineno: int, col: int, section: str, text: str, entity: str) -> dict:
    return {
        "file": path.as_posix(),
        "line": lineno,
        "col": col,
        "section": section,
        "text": text,
        "entity": entity,
    }


# ── emission ────────────────────────────────────────────────────────────────
def _lit(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\").replace('"', '\\"')
        .replace("\n", "\\n").replace("\r", "\\r")
    )
    return f'"{escaped}"'


def mention_triples(mentions: list[dict]) -> str:
    """Sorted N-Triples (valid Turtle) for the mention set.

    The IRI is content-addressed so a re-run supersedes rather than
    accumulates, and the serialization is a sorted set so the output is
    byte-identical for identical inputs — no timestamps, deliberately:
    a timestamp here would break exactly that.
    """
    triples: set[str] = set()
    for m in mentions:
        key = f"{m['file']}|{m['line']}|{m['col']}|{m['entity']}|{m['text']}"
        iri = f"{ONTOLOGY}mention/{hashlib.sha256(key.encode()).hexdigest()[:24]}"
        label = f"'{m['text']}' in {m['file']}:{m['line']} (§ {m['section']})"
        triples.add(f"<{iri}> <{ONTOLOGY}about> <{m['entity']}> .")
        triples.add(f"<{iri}> <{ONTOLOGY}identifier> {_lit(m['text'])} .")
        triples.add(f"<{iri}> <{ONTOLOGY}filePath> {_lit(m['file'])} .")
        triples.add(f'<{iri}> <{ONTOLOGY}sourceKind> "inferred" .')
        triples.add(
            f"<{iri}> <http://www.w3.org/2000/01/rdf-schema#label> {_lit(label)} ."
        )
    return "".join(f"{t}\n" for t in sorted(triples))


# ── the quarantined write ───────────────────────────────────────────────────
def assert_plane_provisioned(graph_iri: str) -> None:
    """The inferred plane must be registered AND labelled before one triple
    moves. Registered-but-unlabelled is quarantine's appearance without its
    substance (planes.py), and this producer will not be the one that fills
    such a graph."""
    listing = _call("/graphs")
    graphs = listing.get("graphs") if isinstance(listing, dict) else None
    if not isinstance(graphs, list):
        raise ExtractError(
            "/graphs returned an unrecognised shape — cannot confirm the "
            "inferred plane is provisioned, so refusing to write"
        )
    for entry in graphs:
        if isinstance(entry, dict) and entry.get("iri") == graph_iri:
            labels = entry.get("labels") or {}
            if labels.get("trust_rank") is None:
                raise ExtractError(
                    f"plane {graph_iri} is registered but carries no trust "
                    "label — quarantine's appearance without its substance. "
                    "Run scripts/planes.py ensure."
                )
            return
    raise ExtractError(
        f"plane {graph_iri} is not provisioned on this store. Run "
        "scripts/planes.py ensure — refusing to write mentions anywhere else."
    )


def write_mentions(turtle: str, graph_iri: str, actor: str, source: str) -> dict:
    result = _call(
        "/knot",
        {"turtle": turtle, "graph": graph_iri, "actor": actor, "source": source},
    )
    if result.get("conforms") is not True or not isinstance(result.get("tx_id"), int):
        raise ExtractError(f"/knot refused the mention write: {result}")
    return result


# ── CLI ─────────────────────────────────────────────────────────────────────
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("files", nargs="+", type=Path, help="markdown files to scan")
    ap.add_argument("--patterns", type=Path,
                    help="JSON config of explicit regex patterns: "
                         '{"patterns": [{"pattern": "...", "entity": "<iri>"}]}')
    ap.add_argument("--dry-run", action="store_true",
                    help="print the would-be triples, write nothing")
    ap.add_argument("--actor", default="camayoc-extract-entities")
    args = ap.parse_args(argv)

    try:
        patterns = load_patterns(args.patterns) if args.patterns else []
        gazetteer, stats = fetch_gazetteer()
        if not gazetteer and not patterns:
            raise ExtractError(
                "the store served no usable labels and no --patterns were "
                "given — there is nothing this producer could deterministically "
                "match, and scanning anyway would report clean prose as fact"
            )
        mentions: list[dict] = []
        per_file: list[tuple[str, int]] = []
        for path in args.files:
            found = scan_file(path, gazetteer, patterns)
            per_file.append((path.as_posix(), len(found)))
            mentions.extend(found)

        # The denominator, on stderr, always — a mention count with no
        # gazetteer size lets a threadbare gazetteer look like clean prose.
        print(
            f"gazetteer: {stats['labels_usable']} usable label(s), "
            f"{stats['labels_skipped_short']} skipped as too short, "
            f"{len(stats['labels_ambiguous'])} ambiguous (matched for nobody: "
            f"{', '.join(stats['labels_ambiguous']) or 'none'}); "
            f"{len(patterns)} explicit pattern(s)",
            file=sys.stderr,
        )
        for name, count in per_file:
            print(f"  {name}: {count} mention(s)", file=sys.stderr)

        turtle = mention_triples(mentions)
        if args.dry_run:
            print(turtle, end="")
            return 0
        if not mentions:
            # The control-negative arm: nothing matched, so nothing is
            # written — not an empty transaction, no writer call at all.
            print("0 mentions — nothing to write, writer not called", file=sys.stderr)
            return 0
        graph_iri = planes.plane_for("inferred")
        assert_plane_provisioned(graph_iri)
        source = "extract-entities:" + ",".join(p.as_posix() for p in args.files)
        result = write_mentions(turtle, graph_iri, args.actor, source)
        print(
            f"{len(mentions)} mention(s) -> {graph_iri} tx={result['tx_id']}",
            file=sys.stderr,
        )
    except (ExtractError, planes.PlaneError) as exc:
        print(f"EXTRACT ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
