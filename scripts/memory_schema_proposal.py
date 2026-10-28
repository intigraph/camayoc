"""Write the agent-memory schema proposal bodies (docs/design/proposals/agent-memory/).

Each JSON file is the exact argument set for quipu_propose_schema_change (or
POST /propose). They are generated from shapes/agent-memory.shapes.ttl so the
production proposal cannot drift from the shapes the create tools use.

    python3 scripts/memory_schema_proposal.py           # regenerate
    python3 scripts/memory_schema_proposal.py --check   # fail if stale
"""
import hashlib, json, pathlib, re, sys, tempfile

CHECK = "--check" in sys.argv[1:]
root = pathlib.Path(__file__).resolve().parent.parent
src = root / "shapes/agent-memory.shapes.ttl"
text = src.read_text()
sha = hashlib.sha256(text.encode()).hexdigest()
final = root / "docs/design/proposals/agent-memory"
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
PROPOSER = "aegis/crew/hammond"
HDR = f"# Generated from shapes/agent-memory.shapes.ttl sha256:{sha}\n# by scripts/memory_schema_proposal.py (aegis-gshu.12). Do not hand-edit.\n"

classes = {
    "Preference": "How a person wants things done in one context (camayoc agent memory).",
    "Handoff": "The one current hand-off note for an agent (camayoc agent memory).",
    "Reference": "Where a system lives and how to reach it (camayoc agent memory).",
    "MemoryTopic": "A named topic that memory records are filed under; its local name is a natural-key segment.",
}
props = ["naturalKey", "recordedAt", "recordedBy", "topic", "rule", "issuedBy", "appliesTo",
         "issuedOn", "why", "verbatim", "person", "prefers", "context", "notWhen", "example",
         "agent", "next", "waitingOn", "handoffContext", "subject", "locator", "secretRef",
         "access", "alias", "supersedes"]
iri_props = {"naturalKey", "topic", "issuedBy", "appliesTo", "person", "agent", "subject", "supersedes"}
onto = [HDR, prefixes, "@prefix owl:   <http://www.w3.org/2002/07/owl#> .\n\n",
        "# No rdfs:domain on any property: a domain would type every subject that uses\n"
        "# the property (the aegis:expiresAt trap, design §7). Shapes carry the per-class\n"
        "# constraints instead.\n\n"]
for c, d in classes.items():
    onto.append(f"aegis:{c} a rdfs:Class , owl:Class ;\n    rdfs:label \"{c}\" ;\n    rdfs:comment \"{d}\" .\n\n")
for p in props:
    kind = "owl:ObjectProperty" if p in iri_props else "owl:DatatypeProperty"
    onto.append(f"aegis:{p} a rdf:Property , {kind} ;\n    rdfs:label \"{p}\" ;\n"
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

def write(fname, kind, target, diff, rationale):
    (out / fname).write_text(json.dumps({"kind": kind, "target": target, "diff": diff,
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
