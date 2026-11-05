"""Write the agent-memory schema proposal bodies (docs/design/proposals/agent-memory/).

Each JSON file is the exact argument set for quipu_propose_schema_change (or
POST /propose). They are generated from shapes/agent-memory.shapes.ttl so the
production proposal cannot drift from the shapes the create tools use.

    python3 scripts/memory_schema_proposal.py           # regenerate
    python3 scripts/memory_schema_proposal.py --check   # fail if stale
"""
import hashlib, json, pathlib, re, sys, tempfile
from rdflib import Graph, URIRef
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from build_stable_namespaces import combined_ledger, remap_graph, shape_bytes, alias_graph

ROOT = pathlib.Path(__file__).resolve().parent.parent
SHAPES = ROOT / "shapes/agent-memory.shapes.ttl"

#: Properties 01-ontology.json declares. quipu.svc does not declare these yet.
PROPS = ["naturalKey", "recordedAt", "recordedBy", "topic", "rule", "issuedBy", "appliesTo",
         "issuedOn", "why", "verbatim", "person", "prefers", "context", "notWhen", "example",
         "agent", "next", "waitingOn", "handoffContext", "subject", "locator", "secretRef",
         "access", "alias", "supersedes", "supersededAt"]
IRI_PROPS = {"naturalKey", "topic", "issuedBy", "appliesTo", "person", "agent", "subject", "supersedes"}

#: Properties the shapes use that quipu.svc already declares (read-only check,
#: 2026-10-28, camayoc#185 review). Canonical aliases are declared only as
#: rdf:Property, preserving their existing object/datatype classification.
ALREADY_SANCTIONED_LIVE = {"about", "chose", "contentHash", "decidedBy", "decidedIn", "over",
                           "rationale", "sourceKind", "supersededBy"}


def shape_paths(text: str) -> set[str]:
    """Every aegis: property the shapes constrain (sh:path aegis:X)."""
    return set(re.findall(r"sh:path aegis:(\w+)", text))


def vocabulary_gaps(text: str) -> list[str]:
    """Shape properties that neither 01 declares nor quipu.svc already sanctions."""
    return sorted(shape_paths(text) - set(PROPS) - ALREADY_SANCTIONED_LIVE)


def stable_profile(text: str, compatibility: bool = False) -> str:
    """Canonical vocabulary, plus explicit legacy validation while reasoning is off.

    Only RDF IRIs change. Literals, individual IRIs and frozen ledgers stay put.
    Compatibility shapes have stable names and explicit alternative paths; no writer
    flip or reactive-materialization assumption is needed for legacy cy records.
    """
    ledger = combined_ledger(ROOT)
    mapping = {r['old']: r['new'] for r in ledger['pairs']}
    source = Graph().parse(data=text, format='turtle')
    result = remap_graph(source, mapping)
    if compatibility:
        from rdflib import BNode, RDF, SH
        from rdflib.collection import Collection
        inverse = {new: old for old, new in mapping.items()}
        # One constraint counts BOTH spellings, including mixed records. Two
        # independent profiles would let a canonical class with legacy fields
        # evade the scoped Directive requirement (or vice versa).
        for shape, pred, value in list(result):
            old = inverse.get(str(value)) if isinstance(value, URIRef) else None
            if not old:
                continue
            legacy = URIRef(old)
            if pred in (SH.targetClass, SH.targetSubjectsOf):
                result.add((shape, pred, legacy))
            elif pred == SH.path:
                path, choices = BNode(), BNode()
                Collection(result, choices, [value, legacy])
                result.remove((shape, pred, value))
                result.add((shape, pred, path))
                result.add((path, SH.alternativePath, choices))
            elif pred == SH['class']:
                choices, left, right = BNode(), BNode(), BNode()
                result.remove((shape, pred, value))
                result.add((shape, SH['or'], choices))
                Collection(result, choices, [left, right])
                result.add((left, SH['class'], value))
                result.add((right, SH['class'], legacy))
    else:
        used = {str(x) for triple in source for x in triple if isinstance(x, URIRef)}
        result += alias_graph({'pairs': [r for r in ledger['pairs'] if r['old'] in used]})
        # Explicit compatibility declarations keep legacy cy writes governed
        # even when the store does not restate new ontology equivalences.
        from rdflib import RDF, RDFS, OWL
        for subject, kind in source.subject_objects(RDF.type):
            if kind in (RDF.Property, RDFS.Class, OWL.Class, OWL.ObjectProperty, OWL.DatatypeProperty):
                result.add((subject, RDF.type, kind))
    return shape_bytes(result, ledger).decode()


def main() -> None:
    CHECK = "--check" in sys.argv[1:]
    gaps = vocabulary_gaps(SHAPES.read_text())
    if gaps:
        sys.exit("shape properties missing from PROPS (the vocabulary gate would refuse them): " + ", ".join(gaps))
    text = SHAPES.read_text()
    sha = hashlib.sha256(text.encode()).hexdigest()
    final = ROOT / "docs/design/proposals/agent-memory"
    out = pathlib.Path(tempfile.mkdtemp()) if CHECK else final
    out.mkdir(parents=True, exist_ok=True)

    prefixes = "".join(l + "\n" for l in text.splitlines() if l.startswith("@prefix"))
    blocks = {}
    for m in re.finditer(r"^(aegis:\w+Shape) a sh:NodeShape ;.*?\]\s*\.\s*$", text, re.S | re.M):
        blocks[m.group(1)] = m.group(0)
    need = ["aegis:MemoryRecordShape", "aegis:MemoryTopicShape", "aegis:MemoryDecisionShape",
            "aegis:MemoryDirectiveShape", "aegis:MemoryPreferenceShape", "aegis:MemoryHandoffShape",
            "aegis:MemoryReferenceShape"]
    missing = [n for n in need if n not in blocks]
    assert not missing, missing

    TRIGGER = "aegis-gshu.12"
    PROPOSER = "aegis/crew/ian"
    HDR = f"# Generated from shapes/agent-memory.shapes.ttl sha256:{sha}\n# by scripts/memory_schema_proposal.py (aegis-gshu.12). Do not hand-edit.\n"

    classes = {
        "Preference": "How a person wants things done in one context (camayoc agent memory).",
        "Handoff": "The one current hand-off note for an agent (camayoc agent memory).",
        "Reference": "Where a system lives and how to reach it (camayoc agent memory).",
        "MemoryTopic": "A named topic that memory records are filed under; its local name is a natural-key segment.",
    }
    onto = [HDR, prefixes, "@prefix owl:   <http://www.w3.org/2002/07/owl#> .\n\n",
            "# No rdfs:domain on any property: a domain would type every subject that uses\n"
            "# the property (the aegis:expiresAt trap, design §7). Shapes carry the per-class\n"
            "# constraints instead.\n\n"]
    classes["Directive"] = "A directive, including legacy Directive individuals, scoped separately for memory records."
    for c, d in classes.items():
        onto.append(f"aegis:{c} a rdfs:Class , owl:Class ;\n    rdfs:label \"{c}\" ;\n    rdfs:comment \"{d}\" .\n\n")
    for p in sorted(set(PROPS) | ALREADY_SANCTIONED_LIVE):
        kind = "owl:ObjectProperty" if p in IRI_PROPS else "owl:DatatypeProperty"
        kinds = "rdf:Property" if p in ALREADY_SANCTIONED_LIVE else "rdf:Property , " + kind
        onto.append(f"aegis:{p} a {kinds} ;\n    rdfs:label \"{p}\" ;\n"
                    f"    rdfs:comment \"camayoc agent-memory field; see shapes/agent-memory.shapes.ttl.\" .\n\n")
    onto_ttl = "".join(onto).replace(prefixes, prefixes + "@prefix rdf:   <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .\n", 1)

    def scoped(name, cls, flags):
        body = blocks[name].replace(name + " a sh:NodeShape ;", name.replace("Shape", "BodyShape") + " a sh:NodeShape ;", 1)
        body = re.sub(r"\n\s*sh:targetClass aegis:\w+ ;", "", body, count=1)
        outer = (f"# Scoped: only memory records (subjects of aegis:naturalKey) typed {cls} are\n"
                 f"# checked. The live {cls} nodes written before camayoc have no naturalKey\n"
                 f"# and stay untouched until someone rewrites them as memory records.\n"
                 f"{name} a sh:NodeShape ;\n"
                 f"    sh:targetSubjectsOf aegis:naturalKey ;\n"
                 f"    sh:or ( [ sh:not [ sh:class {cls} ] ] [ sh:node {name.replace('Shape', 'BodyShape')} ] ) ;\n"
                 f"    sh:message \"A memory {cls.split(':')[1]} needs {flags}. Run the cy create tool with --help for each field.\" .\n")
        return body + "\n\n" + outer

    new_ttl = HDR + prefixes + "\n" + "\n\n".join(blocks[n] for n in [
        "aegis:MemoryRecordShape", "aegis:MemoryTopicShape", "aegis:MemoryPreferenceShape",
        "aegis:MemoryHandoffShape", "aegis:MemoryReferenceShape"]) + "\n"
    dir_ttl = HDR + prefixes + "\n" + scoped("aegis:MemoryDirectiveShape", "aegis:Directive",
        "--rule, --issued-by, --applies-to and --topic, each exactly once") + "\n"
    dec_ttl = HDR + prefixes + "\n" + scoped("aegis:MemoryDecisionShape", "aegis:Decision",
        "--chose, --decided-by, --about and --topic, each exactly once") + "\n"

    # Publish only the approved scoped profiles in the core namespace. The
    # complete tool-source file also contains the held Decision class-wide gate.
    scoped_source = ROOT / "shapes/agent-memory-scoped.shapes.ttl"
    scoped_text = new_ttl + "\n" + dir_ttl
    if CHECK:
        if not scoped_source.exists() or scoped_source.read_text() != scoped_text:
            sys.exit("stale scoped memory source; regenerate proposal bodies")
    else:
        scoped_source.write_text(scoped_text)

    # Legacy source is retained for the additive namespace builder, never loaded.
    legacy_ontology = ROOT / "ontology/agent-memory.ttl"
    if CHECK:
        if not legacy_ontology.exists() or legacy_ontology.read_text() != onto_ttl:
            sys.exit("stale ontology/agent-memory.ttl; regenerate proposal bodies")
    else:
        legacy_ontology.write_text(onto_ttl)

    def write(fname, kind, target, diff, rationale):
        (out / fname).write_text(json.dumps({"kind": kind, "target": target, "diff": stable_profile(diff, kind == "shape"),
            "proposer": PROPOSER, "rationale": rationale, "trigger_ref": TRIGGER}, indent=2) + "\n")

    write("01-ontology.json", "ontology", "camayoc-agent-memory-vocabulary", onto_ttl,
          "Sanction the camayoc agent-memory classes and properties (design §7.1 item 1) so the vocabulary gate admits cy memory writes. No rdfs:domain, so nothing existing gets retyped.")
    write("02-shapes-new-classes.json", "shape", "camayoc-agent-memory", new_ttl,
          "Shapes for the new memory classes plus the tool-filled record fields (design §7.1 item 1). None of these classes has live nodes, and MemoryRecordShape targets subjects of aegis:naturalKey, which no live node carries.")
    write("03-shape-directive-scoped.json", "shape", "camayoc-agent-memory-directive", dir_ttl,
          "Tighten aegis:Directive for memory records only (design §7.1 item 2, scoping option). The live census found 923 asserted aegis:Directive nodes (1157 with RDFS subclasses), and 0 of them meet the required fields, so a class-wide shape would refuse any rewrite of all of them.")
    write("04-shape-decision-scoped.json", "shape", "camayoc-agent-memory-decision", dec_ttl,
          "Memory Decision shape, scoped like the Directive one (design §7.1 item 3). Load it only after quipu PR #911 (GateApproval split) is deployed on quipu.svc and its subClassOf data step is done.")
    (out / "SOURCE_SHA256").write_text(sha + "  shapes/agent-memory.shapes.ttl\n")
    if CHECK:
        stale = [p.name for p in sorted(out.iterdir())
                 if not (final / p.name).exists() or (final / p.name).read_text() != p.read_text()]
        if stale:
            sys.exit("stale proposal bodies, rerun scripts/memory_schema_proposal.py: " + ", ".join(stale))
    print("ok", sha)


if __name__ == "__main__":
    main()
