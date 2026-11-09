"""Compare isolated identity data with no key policy and with this profile."""
from pathlib import Path

from pyshacl import validate
from rdflib import Graph, Literal, Namespace, RDF, URIRef

HERE = Path(__file__).resolve().parent
CORE = Namespace("https://w3id.org/intigraph/camayoc/core/ns#")
INFRA = Namespace("https://w3id.org/intigraph/camayoc/homelab-infra/ns#")
SUBJECT = URIRef("urn:fixture:identity")


def main():
    shapes = Graph().parse(HERE / "keys.ttl")
    host = Graph()
    host.add((SUBJECT, RDF.type, CORE.Host))
    host.add((SUBJECT, INFRA.identitySite, Literal("fixture-site")))
    host.add((SUBJECT, INFRA.identityAsset, Literal("fixture-asset")))
    missing = Graph() + host
    missing.remove((SUBJECT, INFRA.identitySite, None))
    alias = Graph() + host
    alias.add((SUBJECT, RDF.type, INFRA.TerraformResource))
    for label, data in [("complete host", host), ("host missing site", missing),
                        ("host aliases Terraform declaration", alias)]:
        before = validate(data, shacl_graph=Graph())[0]
        after = validate(data, shacl_graph=shapes)[0]
        print(f"{label}: no key policy={before}; identity profile={after}")
    print("Disposable RDF only; no namespace enrollment, mint or installed effect.")


if __name__ == "__main__":
    main()
