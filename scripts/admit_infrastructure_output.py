#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["rdflib==7.6.0"]
# ///
"""Offline exact-batch admission; never writes a graph or fetches a source URI."""
# arming: agent-invoked; explicit offline contract check, no scheduled producer.
from __future__ import annotations

import argparse
import ipaddress
import json
import subprocess
import tempfile
from pathlib import Path

from rdflib import Graph, Literal, Namespace, RDF, RDFS, URIRef

CORE = Namespace("https://w3id.org/intigraph/camayoc/core/ns#")
INFRA = Namespace("https://w3id.org/intigraph/camayoc/homelab-infra/ns#")
CRTV = Namespace("http://open-services.net/ns/crtv#")
PROV = Namespace("http://www.w3.org/ns/prov#")
SCHEMA = Namespace("https://schema.org/")
DCT = Namespace("http://purl.org/dc/terms/")
ALLOWED_TYPES = {
    CORE.Host, CORE.BareMetalHost, CORE.ProxmoxNode, CORE.LXCContainer,
    CORE.Service, CORE.SystemdService, CORE.AnsibleGroup, CORE.AnsibleRole,
    INFRA.TerraformResource, INFRA.ReverseProxyRoute, CRTV.IPAddress,
    PROV.Entity, PROV.Activity,
}


def one(graph: Graph, subject: URIRef, predicate: URIRef):
    values = list(graph.objects(subject, predicate))
    if len(values) != 1:
        raise ValueError("required field does not have exactly one value")
    return values[0]


def check_subject_inventory(graph: Graph, records: set) -> None:
    """Assign subjects by profile roles, never by arbitrary URI reachability.

    Locator and address-space IRIs are terminal references. Host/service
    references can carry their required type only, unless independently bound
    as a primary subject by another admitted record. Native SHACL validates
    the complete statements of every record, primary subject and support node.
    """
    subjects = {one(graph, record, SCHEMA.about) for record in records}
    sources = {one(graph, record, PROV.wasDerivedFrom) for record in records}
    activities = {one(graph, record, PROV.wasGeneratedBy) for record in records}
    addresses = {address for subject in subjects
                 for address in graph.objects(subject, CRTV.ipAddress)}
    inventory = {}
    roles = [
        (records, {RDF.type, DCT.conformsTo, SCHEMA.about, CORE.sourceKind,
                   CORE.contentSha256, PROV.wasDerivedFrom, PROV.wasGeneratedBy},
         {PROV.Entity}),
        (subjects, {RDF.type, SCHEMA.identifier, RDFS.label, CRTV.ipAddress,
                    INFRA.hostname, INFRA.ctId, CORE.runs_on, CORE.routes_to},
         ALLOWED_TYPES - {PROV.Entity, PROV.Activity, CRTV.IPAddress}),
        (sources, {RDF.type, DCT.identifier, CORE.contentSha256, PROV.atLocation},
         {PROV.Entity}),
        (activities, {RDF.type, PROV.used, DCT.identifier}, {PROV.Activity}),
        (addresses, {RDF.type, CRTV.address, CRTV.contextAddressSpace}, {CRTV.IPAddress}),
    ]
    for members, predicates, kinds in roles:
        for member in members:
            if not isinstance(member, URIRef) or member in inventory:
                raise ValueError("support subject has an invalid or conflicting role")
            types = set(graph.objects(member, RDF.type))
            if not types or not types <= kinds:
                raise ValueError("subject kind is incompatible with its output role")
            inventory[member] = predicates
    for relation, kinds in [
        (CORE.runs_on, {CORE.Host, CORE.BareMetalHost, CORE.ProxmoxNode}),
        (CORE.routes_to, {CORE.Service, CORE.SystemdService}),
    ]:
        for subject in subjects:
            for reference in graph.objects(subject, relation):
                if reference in subjects:
                    continue  # Its own admitted record supplies full validation.
                if not isinstance(reference, URIRef):
                    raise ValueError("host/service reference must be an IRI")
                types = set(graph.objects(reference, RDF.type))
                if not types or not types <= kinds:
                    raise ValueError("reference carries an unsupported entity kind")
                if reference in inventory and inventory[reference] != {RDF.type}:
                    raise ValueError("reference conflicts with a support subject role")
                inventory[reference] = {RDF.type}
    for subject, predicate, _ in graph:
        if subject not in inventory or predicate not in inventory[subject]:
            raise ValueError("statement outside the role-bounded subject inventory")


def admit(graph: Graph, expected: dict) -> int:
    """Bind exact records/subjects/keys before trusting SHACL conformance."""
    if not isinstance(expected, dict) or not expected:
        raise ValueError("expected record map must be nonempty")
    marked = set(graph.subjects(DCT.conformsTo, INFRA.ConnectorRecordShape))
    if marked != {URIRef(record) for record in expected}:
        raise ValueError("record membership or profile marker mismatch")
    identities = {}
    for record, binding in expected.items():
        if not isinstance(binding, dict) or set(binding) != {"subject", "identifier"}:
            raise ValueError("each expected record binds a subject and scoped identifier")
        if not all(isinstance(value, str) and value for value in binding.values()):
            raise ValueError("empty or non-string identity binding")
        subject = one(graph, URIRef(record), SCHEMA.about)
        if subject != URIRef(binding["subject"]):
            raise ValueError("record subject differs from its admitted binding")
        identifier = one(graph, subject, SCHEMA.identifier)
        if not isinstance(identifier, Literal) or str(identifier) != binding["identifier"]:
            raise ValueError("scoped identifier mismatch")
        if str(identifier) in identities and identities[str(identifier)] != subject:
            raise ValueError("one scoped key identifies multiple subjects")
        identities[str(identifier)] = subject
        activity = one(graph, URIRef(record), PROV.wasGeneratedBy)
        source = one(graph, URIRef(record), PROV.wasDerivedFrom)
        if source != one(graph, activity, PROV.used):
            raise ValueError("record source differs from extraction input")
    for _, kind in graph.subject_objects(RDF.type):
        if kind not in ALLOWED_TYPES:
            raise ValueError("unexpected RDF type in producer batch")
    addresses = set(graph.subjects(RDF.type, CRTV.IPAddress))
    addresses.update(graph.objects(None, CRTV.ipAddress))
    for address in addresses:
        lexical = one(graph, address, CRTV.address)
        scope = one(graph, address, CRTV.contextAddressSpace)
        if not isinstance(lexical, Literal) or not isinstance(scope, URIRef):
            raise ValueError("address needs a literal and IRI address-space scope")
        text = str(lexical)
        if "%" in text or str(ipaddress.ip_address(text)) != text:
            raise ValueError("address must be canonical, unscoped IP text")
    check_subject_inventory(graph, marked)
    return len(marked)


def bounded(path: Path) -> bytes:
    with path.open("rb") as source:
        data = source.read(4 * 1024 * 1024 + 1)
    if len(data) > 4 * 1024 * 1024:
        raise ValueError("input exceeds four MiB")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data", type=Path)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--quipu", type=Path, required=True)
    args = parser.parse_args()
    try:
        if not args.quipu.is_absolute() or not args.quipu.is_file():
            raise ValueError("--quipu must name an existing absolute executable")
        payload = bounded(args.data)
        graph = Graph().parse(data=payload.decode("utf-8"), format="turtle")
        count = admit(graph, json.loads(bounded(args.manifest)))
        shapes = Path(__file__).resolve().parents[1] / "profiles/infrastructure-output/shapes.ttl"
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / "data.ttl"
            snapshot.write_bytes(payload)
            result = subprocess.run(
                [str(args.quipu), "validate", "--shapes", str(shapes), "--data", str(snapshot)],
                capture_output=True, text=True, timeout=30, check=False,
            )
        if result.returncode or not result.stdout.lstrip().startswith("valid"):
            raise ValueError("native SHACL validation refused or could not run")
        print(json.dumps({"result": "ADMITTED_OFFLINE", "records": count}))
        return 0
    except Exception:
        # Never echo arbitrary source bytes or secret-bearing RDF values.
        print(json.dumps({"result": "REFUSED", "reason": "input or validation contract failed"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
