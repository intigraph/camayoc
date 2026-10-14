# /// script
# requires-python = ">=3.11"
# dependencies = ["rdflib==7.6.0"]
# ///
"""Read-only source coverage demo; no server or policy activation."""
import subprocess
from pathlib import Path
from rdflib import Graph, Namespace, RDF, RDFS, SH
ROOT = Path(__file__).resolve().parents[3]
Q = Namespace("https://scbrown.github.io/quechua/ns#")
NAMES = ("Directive", "Policy", "TextRule", "InternalIdentifierPattern",
         "OperatingPoint", "Reaction", "AdmissionPolicy", "Decision", "DecisionRecord")
BASE = "83673d40acc7494d66bb545002b62636b7684b93"
KIND = "camayoc"
def blob(path):
    p = subprocess.run(["git", "-C", str(ROOT), "show", BASE + ":" + path], capture_output=True)
    return p.stdout.decode() if p.returncode == 0 else None

def report(label, catalog, shapes, loader):
    if catalog is None:
        print(f"{label}: governance catalogue absent; loader={loader}")
        return
    g = Graph().parse(data=catalog, format="turtle")
    s = Graph()
    for text in shapes:
        s.parse(data=text, format="turtle")
    declared = sum((Q[n], RDF.type, RDFS.Class) in g for n in NAMES)
    targets = sum(bool(list(s.subjects(SH.targetClass, Q[n]))) for n in NAMES)
    positive = (Q.Directive, RDF.type, RDFS.Class) in g
    print(f"{label}: declared={declared}/9 directly_shaped={targets}/9 Directive_control={positive} loader={loader}")
if KIND == "quechua":
    paths = ["shapes/" + n + ".shapes.ttl" for n in
             ("action-governance", "observation-governance", "work-item", "ontology", "camayoc")]
    report("before " + BASE[:8], blob("ns.ttl"), [blob(p) for p in paths], False)
    report("candidate source", (ROOT / "ns.ttl").read_text(),
           [(ROOT / "profiles/governance/shapes.ttl").read_text()], False)
else:
    report("before " + BASE[:8], blob("governance/catalog.ttl"), [],
           blob("scripts/governance_loader.py") is not None)
    report("candidate source", (ROOT / "governance/catalog.ttl").read_text(),
           [(ROOT / "governance/shapes.ttl").read_text()],
           (ROOT / "scripts/governance_loader.py").exists())
print("Source fixture only: no historical lineage, certification, deployment or activation claim.")
