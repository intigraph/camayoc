# Design: prose entity mentions — deterministic, still quarantined

> **Implementation status (2026-08-31):** ✅ **Implemented as
> `scripts/extract_entities.py`** (camayoc-0c8, gap G4 of the txtai
> comparison, tambo `docs/design/txtai-gap-analysis.md`).
> `tests/test_extract_entities.py` pins the promises below.

txtai ships NER pipelines; camayoc's ingress rule 4 already defines the slot —
inference is quarantined, not banned. This producer fills it with **no model
in the loop**: mentions come from (a) a gazetteer of the graph's own labels
(`rdfs:label`, `skos:prefLabel`, `skos:altLabel`, fetched via SPARQL) and
(b) explicit user-supplied regex patterns, each declared with the entity IRI
it stands for.

## Why deterministic output still lands in `crew:inferred`

The extraction is reproducible, but the *claim* — "this prose is about that
entity" — is a reading of the text, not a record of an event. A string equal
to a label is evidence, not identity. So every mention routes through
`planes.plane_for("inferred")`, tagged `aegis:sourceKind "inferred"` — never
ROOT, never an observed plane — and can earn promotion the governed way
(`scripts/promote_plane.py`) like anything else in quarantine.

## Abstentions, counted out loud

- A gazetteer label naming **two entities matches for nobody** — a wrong
  mention silently attaches prose to the wrong referent, worse than a gap.
- Labels under 3 characters are skipped (they match everything).
- Matching is whole-word and case-sensitive; case-folding is a guess this
  producer does not make.
- Fenced code blocks are not prose.

Every abstention is counted on stderr, alongside the gazetteer size — the
ingest-git lesson that a silent abstention is indistinguishable from coverage.

## No new ontology terms

The competency rule is not triggered: a mention is a **generic fact over
existing predicates** — `aegis:about` (the matched entity), `aegis:identifier`
(the surface text), `aegis:filePath` (where), `rdfs:label` (line and section,
human-readable), `aegis:sourceKind`. No class is minted for it; if mentions
later need their own query surface, that is the moment a competency question
earns one.

## Refusals

An unreachable store, a gazetteer that cannot be read, an unprovisioned
inferred plane, or a plane registered but not labelled all **exit nonzero
before anything is written** — could not look is not zero mentions, and a
registered-but-unlabelled plane is quarantine's appearance without its
substance (`scripts/planes.py`). Zero mentions writes nothing at all: the
writer is never called, not called with an empty body. `--dry-run` prints the
would-be triples (sorted N-Triples; byte-identical on re-run) and writes
nothing.
