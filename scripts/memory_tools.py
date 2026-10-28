#!/usr/bin/env python3
"""Generate the agent-memory tool surfaces from one definition per entity.

docs/design/agent-memory-entities.md §3. The shape
(`shapes/agent-memory.shapes.ttl`) owns every field: its argument name
(`sh:name`), help (`sh:description`) and plain-word refusal (`sh:message`).
The sidecar (`tools/memory/<entity>.tool.json`) owns only tool facts: verb,
noun, natural-key parts, sourceKind rule, lifecycle defaults, recall queries
and one worked example. It holds no field definitions.

This script merges the two and writes every generated surface. With
`--check` it writes nothing and exits 1 when any surface on disk differs from
what the definitions generate, naming the file and the command that fixes it.

    python3 scripts/memory_tools.py          # regenerate
    python3 scripts/memory_tools.py --check  # CI drift gate
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHAPES = ROOT / "shapes" / "agent-memory.shapes.ttl"
SIDECARS = ROOT / "tools" / "memory"
DEFINITIONS = ROOT / "camayoc_cli" / "data" / "definitions.json"
QUERIES = ROOT / "queries"
PRIMER = ROOT / "camayoc_cli" / "primer.md"

AEGIS = "http://aegis.gastown.local/ontology/"
SH = "http://www.w3.org/ns/shacl#"
PREFIXES = {
    "aegis": AEGIS,
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "prov": "http://www.w3.org/ns/prov#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "sh": SH,
}
#: Keys a sidecar may carry. Anything else (above all a field definition) is a
#: lint failure: fields belong to the shape, or the two drift.
SIDECAR_KEYS = {"entity", "class", "shape", "verb", "noun", "summary", "body", "key",
                "issuer", "sourceKind", "lifecycle", "recall", "example"}
RECALL_KEYS = {"name", "verb", "summary", "filters", "history", "fresh", "alsoMatch"}
RANK = {"declared": 0, "observed": 1, "inferred": 2}  # §2.2: declared > records > inferred
REGEN = "python3 scripts/memory_tools.py"


class DefinitionError(ValueError):
    """The shape or a sidecar breaks the tool contract."""


def kebab(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "-", name).lower()


def curie(iri: str) -> str:
    for prefix, ns in PREFIXES.items():
        if iri.startswith(ns):
            return f"{prefix}:{iri[len(ns):]}"
    return iri


def expand(value: str) -> str:
    prefix, _, local = value.partition(":")
    return PREFIXES[prefix] + local if prefix in PREFIXES and not local.startswith("//") else value


# ---- reading ---------------------------------------------------------------

def read_shapes(path: Path = SHAPES) -> dict[str, dict]:
    """NodeShape IRI -> {target, fields}. Field order follows sh:order."""
    import rdflib
    from rdflib.collection import Collection

    g = rdflib.Graph()
    g.parse(path, format="turtle")
    sh = rdflib.Namespace(SH)
    out = {}
    for shape in sorted(set(g.subjects(rdflib.RDF.type, sh.NodeShape)), key=str):
        target = g.value(shape, sh.targetClass)
        fields = []
        for prop in g.objects(shape, sh.property):
            def one(p, prop=prop):
                v = g.value(prop, p)
                return None if v is None else v.toPython() if isinstance(v, rdflib.Literal) else str(v)
            enum = g.value(prop, sh["in"])
            name = one(sh.name)
            field = {
                "name": name,
                "path": curie(one(sh.path) or ""),
                "flag": f"--{kebab(name)}" if name else None,
                "required": int(one(sh.minCount) or 0) >= 1,
                "maxCount": None if one(sh.maxCount) is None else int(one(sh.maxCount)),
                "kind": ("enum" if enum is not None else
                         "iri" if one(sh.nodeKind) == SH + "IRI" else "text"),
                "datatype": curie(one(sh.datatype)) if one(sh.datatype) else None,
                "pattern": one(sh.pattern),
                "enum": [str(x) for x in Collection(g, enum)] if enum is not None else None,
                "description": one(sh.description),
                "message": one(sh.message),
                "order": int(one(sh.order) or 0),
                "_minCount": one(sh.minCount),
            }
            field["repeatable"] = field["maxCount"] != 1
            fields.append(field)
        fields.sort(key=lambda f: (f["order"], f["name"] or ""))
        out[curie(str(shape))] = {"target": curie(str(target)) if target else None, "fields": fields}
    return out


def read_sidecars(directory: Path = SIDECARS) -> dict[str, dict]:
    out = {}
    for path in sorted(directory.glob("*.tool.json")):
        car = json.loads(path.read_text())
        out[car.get("entity", path.name)] = car
    return out


# ---- lint ------------------------------------------------------------------

def lint(shapes: dict[str, dict], sidecars: dict[str, dict]) -> list[str]:
    """Every contract violation, in plain words. Empty means the build may proceed."""
    errors = []
    entity_shapes = {car.get("shape") for car in sidecars.values()}
    for shape_iri, shape in shapes.items():
        for f in shape["fields"]:
            where = f"{shape_iri} {f['path']}"
            if f["_minCount"] is not None and not f["message"]:
                errors.append(f"{where}: minCount without sh:message (the 10/27 unnamed-field bug)")
            for key in ("name", "description", "message"):
                if not f[key]:
                    errors.append(f"{where}: missing sh:{key}")
            if shape_iri in entity_shapes and f["required"] and f["message"] and f["flag"] \
                    and f["flag"] not in f["message"]:
                errors.append(f"{where}: refusal must name its flag {f['flag']}")
    for entity, car in sidecars.items():
        where = f"tools/memory/{entity}.tool.json"
        extra = set(car) - SIDECAR_KEYS
        if extra:
            errors.append(f"{where}: keys not allowed in a sidecar (fields live in the shape): {sorted(extra)}")
        shape = shapes.get(car.get("shape", ""))
        if shape is None:
            errors.append(f"{where}: shape {car.get('shape')} not in {SHAPES.name}")
            continue
        if shape["target"] != car.get("class"):
            errors.append(f"{where}: class {car.get('class')} but shape targets {shape['target']}")
        names = {f["name"] for f in shape["fields"]}
        refs = list(car.get("key", [])) + [car.get("body")] + ([car["issuer"]] if car.get("issuer") else [])
        for r in car.get("recall", []):
            refs += r.get("filters", [])
            bad = set(r) - RECALL_KEYS
            if bad:
                errors.append(f"{where}: recall {r.get('name')} has unknown keys {sorted(bad)}")
            if not re.fullmatch(r"mem_[a-z_]+", r.get("name", "")):
                errors.append(f"{where}: recall query names start with mem_")
        refs += list(car.get("example", {}))
        for r in refs:
            if r not in names:
                errors.append(f"{where}: '{r}' is not a field of {car['shape']}")
        for k in car.get("key", []):
            f = next((f for f in shape["fields"] if f["name"] == k), None)
            if f and not (f["required"] and f["maxCount"] == 1):
                errors.append(f"{where}: key part '{k}' must be required and single-valued")
        missing = [f["name"] for f in shape["fields"] if f["required"] and f["name"] not in car.get("example", {})]
        if missing:
            errors.append(f"{where}: example lacks required fields {missing}")
    return errors


# ---- generation ------------------------------------------------------------

def recall_template(car: dict, fields: dict[str, dict], recall: dict) -> tuple[str, list[dict]]:
    """A stored-query template with the §2.2 recall filters, and its params."""
    cls = car["class"]
    body = fields[car["body"]]["path"]
    params = [{"name": n, "type": "iri", "required": True,
               "description": f"The crew:{n} plane graph IRI (filled by cy from its plane table)."}
              for n in ("declared", "records", "inferred")]
    where = [f"?iri a {cls} ; aegis:naturalKey ?naturalKey ; rdfs:label ?label ; "
             f"aegis:sourceKind ?sourceKind ; aegis:recordedAt ?recordedAt ; {body} ?b ."]
    if recall.get("history"):
        params.append({"name": "key", "type": "iri", "required": True,
                       "description": "The natural-key IRI whose versions to list."})
        where.append("FILTER(?naturalKey = <{key}>)")
    else:
        for name in recall.get("filters", []):
            f = fields[name]
            params.append({"name": name, "type": "text", "required": True,
                           "description": f"{f['description']} Empty string matches any."})
            var = f"?f_{name}"
            also = f" || {var} = {recall['alsoMatch']}" if recall.get("alsoMatch") else ""
            where.append(f"?iri {f['path']} {var} .")
            where.append(f"FILTER('{{{name}}}' = '' || STR({var}) = '{{{name}}}'{also})")
        where.append("FILTER NOT EXISTS { ?iri aegis:supersededBy ?newer }")
        where.append("FILTER NOT EXISTS { ?iri aegis:expiresAt ?expires . FILTER(STR(?expires) < '{now}') }")
        params.append({"name": "now", "type": "text", "required": True,
                       "description": "Current UTC instant (ISO-8601); records past expiresAt drop out."})
        if recall.get("fresh"):
            where.append("FILTER(STR(?recordedAt) >= '{fresh_since}')")
            params.append({"name": "fresh_since", "type": "text", "required": True,
                           "description": "Oldest recordedAt still inside maxAge (ISO-8601, filled by cy)."})
    where.append("OPTIONAL { ?iri aegis:supersedes ?supersedes }")
    rank = " ".join(f'IF(?sourceKind = "{k}", {v},' for k, v in list(RANK.items())[:-1])
    where.append(f"BIND({rank} {RANK['inferred']}{')' * (len(RANK) - 1)} AS ?rank)")
    order = "DESC(?recordedAt)" if recall.get("history") else "?rank DESC(?recordedAt)"
    template = (
        f"PREFIX aegis: <{AEGIS}> PREFIX rdfs: <{PREFIXES['rdfs']}> "
        f"SELECT ?iri ?naturalKey (\"{car['entity']}\" AS ?kind) ?label "
        f"(GROUP_CONCAT(DISTINCT STR(?b); separator=\" | \") AS ?body) ?sourceKind ?recordedAt ?supersedes "
        "FROM <{declared}> FROM <{records}> FROM <{inferred}> "
        f"WHERE {{ {' '.join(where)} }} "
        "GROUP BY ?iri ?naturalKey ?label ?sourceKind ?recordedAt ?supersedes ?rank "
        f"ORDER BY {order} ?iri LIMIT 51"
    )
    return template, params


DEFAULT_PLANES = {"declared": "https://camayoc.local/plane/crew/declared",
                  "records": "https://camayoc.local/plane/crew/records",
                  "inferred": "https://camayoc.local/plane/crew/inferred"}


def index_since(doc: dict) -> dict:
    """`mem_index_since`: the one paged query bobbin's memory mirror consumes (gshu.2).

    Contract: bobbin src/memory_recall.rs and its stub
    tests/fixtures/agent-memory/mem_index_since.json. Called with only
    `since`, so the plane graphs are optional params defaulting to camayoc's
    default planes. Every current record comes back with status `current` at
    its own recordedAt; a superseded one comes back with status `superseded`
    at the recordedAt of the record that replaced it, so the mirror deletes
    it. status and cursor come from a UNION of the two branches, never from
    IF/COALESCE, which older quipu SPARQL leaves unbound. The body is each
    entity's own body field (chose, rule, prefers, locator, next).

    The cursor is `<recordedAt>|<iri>` (recordedAt is fixed-width UTC, so the
    string orders by recordedAt, then iri): records sharing a timestamp never
    fall between two pages, and `since` carries both parts.
    """
    bodies = sorted({f["path"] for e in doc["entities"].values() for f in e["fields"]
                     if f["name"] == e["body"]})
    params = [{"name": "since", "type": "text", "required": False, "default": "",
               "description": "The cursor of the last row of the previous page, '<recordedAt>|<iri>'; empty for a full pull."}]
    params += [{"name": n, "type": "iri", "required": False, "default": iri,
                "description": f"The crew:{n} plane graph IRI; pass it when CAMAYOC_PLANE_NS is not the default."}
               for n, iri in DEFAULT_PLANES.items()]
    template = (
        f"PREFIX aegis: <{AEGIS}> PREFIX rdfs: <{PREFIXES['rdfs']}> "
        "SELECT ?iri ?naturalKey ?kind ?label (GROUP_CONCAT(DISTINCT STR(?b); separator=\" | \") AS ?body) "
        "?sourceKind ?recordedAt ?reviewAfter ?expiresAt ?status ?cursor "
        "FROM <{declared}> FROM <{records}> FROM <{inferred}> WHERE { "
        "?iri aegis:naturalKey ?naturalKey ; a ?kind ; rdfs:label ?label ; aegis:recordedAt ?recordedAt . "
        f"OPTIONAL {{ VALUES ?bp {{ {' '.join(bodies)} }} ?iri ?bp ?b }} "
        "OPTIONAL { ?iri aegis:sourceKind ?sourceKind } "
        "OPTIONAL { ?iri aegis:reviewAfter ?reviewAfter } "
        "OPTIONAL { ?iri aegis:expiresAt ?expiresAt } "
        "{ ?iri aegis:recordedAt ?currentAt . FILTER NOT EXISTS { ?iri aegis:supersededBy ?anyNewer } "
        "BIND(\"current\" AS ?status) BIND(CONCAT(STR(?currentAt), \"|\", STR(?iri)) AS ?cursor) } UNION "
        "{ ?iri aegis:supersededBy ?newer . ?newer aegis:recordedAt ?supersededAt . "
        "BIND(\"superseded\" AS ?status) BIND(CONCAT(STR(?supersededAt), \"|\", STR(?iri)) AS ?cursor) } "
        "FILTER(?cursor > '{since}') } "
        "GROUP BY ?iri ?naturalKey ?kind ?label ?sourceKind ?recordedAt ?reviewAfter ?expiresAt ?status ?cursor "
        "ORDER BY ?cursor LIMIT 51"
    )
    return {"action": "load", "name": "mem_index_since",
            "description": "Memory records changed since a cursor, for bobbin's memory mirror: current ones to "
                           "upsert, superseded ones to delete. cursor = '<recordedAt>|<iri>' (for a superseded row, the "
                           "replacing record's recordedAt and the superseded iri), ordered by recordedAt then iri; "
                           "pass the last row's cursor back as since. 50 rows plus one overflow sentinel. "
                           "Generated by scripts/memory_tools.py.",
            "template": template, "params": params}


def build(shapes: dict[str, dict], sidecars: dict[str, dict]) -> tuple[dict, dict[str, dict]]:
    """(definitions document, {query file name: stored-query manifest})."""
    def public(f):
        return {k: v for k, v in f.items() if not k.startswith("_") and k != "order"}

    shared = [public(f) for f in shapes["aegis:MemoryRecordShape"]["fields"]]
    entities, queries = {}, {}
    for entity, car in sorted(sidecars.items()):
        fields = [public(f) for f in shapes[car["shape"]]["fields"]]
        by_name = {f["name"]: f for f in fields}
        recalls = []
        for r in car.get("recall", []):
            template, params = recall_template(car, by_name, r)
            manifest = {"action": "load", "name": r["name"],
                        "description": f"{r['summary']} Generated from tools/memory/{entity}.tool.json; "
                                       "drops superseded and expired records, ranks declared > observed > inferred, "
                                       "at most 50 rows plus one overflow sentinel.",
                        "template": template, "params": params}
            queries[f"{r['name']}.json"] = manifest
            recalls.append({**r, "params": params, "template": template})
        entities[entity] = {**{k: v for k, v in car.items() if k != "recall"},
                            "fields": fields, "recall": recalls}
    doc = {
        "schema": "camayoc-memory-tools/1",
        "generated_by": REGEN,
        "sources": {"shapes": str(SHAPES.relative_to(ROOT)), "sidecars": str(SIDECARS.relative_to(ROOT))},
        "namespace": AEGIS,
        "prefixes": {k: v for k, v in PREFIXES.items() if k != "sh"},
        "rank": RANK,
        "shared": shared,
        "entities": entities,
    }
    queries["mem_index_since.json"] = index_since(doc)
    return doc, queries


def replace_region(text: str, name: str, body: str) -> str:
    """Rewrite the `<!-- BEGIN generated NAME -->` region of a hand-written file."""
    begin, end = f"<!-- BEGIN generated {name} -->", f"<!-- END generated {name} -->"
    if begin not in text or end not in text:
        raise DefinitionError(f"generated region '{name}' markers missing")
    head, rest = text.split(begin, 1)
    _, tail = rest.split(end, 1)
    return f"{head}{begin}\n{body.rstrip()}\n{end}{tail}"


def primer_entities(doc: dict) -> str:
    rows = ["| Entity | Record | Required flags | Natural key | Recall |", "| --- | --- | --- | --- | --- |"]
    for name, e in doc["entities"].items():
        required = " ".join(f"`{f['flag']}`" for f in e["fields"] if f["required"])
        flags = {f["name"]: f["flag"] for f in e["fields"]}
        key = " + ".join(flags[k].lstrip("-") for k in e["key"])
        recall = "; ".join(f"`cy recall {name}" + (" --history <key>`" if r.get("history") else
                           "".join(f" [{flags[x]}]" for x in r.get("filters", [])) + "`")
                           for r in e["recall"])
        rows.append(f"| {name} | `cy record {name}` | {required} | {key} | {recall} |")
    return "\n".join(rows)


def render_json(obj) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def outputs(shapes: dict | None = None, sidecars: dict | None = None) -> dict[Path, str]:
    """Every generated file and its exact content."""
    shapes = read_shapes() if shapes is None else shapes
    sidecars = read_sidecars() if sidecars is None else sidecars
    errors = lint(shapes, sidecars)
    if errors:
        raise DefinitionError("\n".join(errors))
    doc, queries = build(shapes, sidecars)
    files = {DEFINITIONS: render_json(doc)}
    for name, manifest in queries.items():
        files[QUERIES / name] = render_json(manifest)
    if PRIMER.exists():
        files[PRIMER] = replace_region(PRIMER.read_text(), "entities", primer_entities(doc))
    return files


def drift(files: dict[Path, str]) -> list[str]:
    out = []
    for path, content in sorted(files.items()):
        rel = path.relative_to(ROOT)
        if not path.exists():
            out.append(f"{rel}: missing")
        elif path.read_text() != content:
            out.append(f"{rel}: differs from what the definitions generate")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="write nothing; exit 1 on drift")
    args = ap.parse_args(argv)
    try:
        files = outputs()
    except DefinitionError as exc:
        print(f"memory tool definitions refused:\n{exc}", file=sys.stderr)
        return 2
    if args.check:
        stale = drift(files)
        if stale:
            print("generated surfaces drifted from shapes/agent-memory.shapes.ttl + tools/memory/:", file=sys.stderr)
            for line in stale:
                print(f"  {line}", file=sys.stderr)
            print(f"regenerate with: {REGEN}", file=sys.stderr)
            return 1
        print(f"memory tool surfaces match their definitions ({len(files)} files)")
        return 0
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.read_text() != content:
            path.write_text(content)
            print(f"wrote {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
