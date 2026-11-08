#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["rdflib==7.6.0"]
# ///
"""Project local inventory exports into offline, explicitly addressed records.

No source fetch, inventory plugin execution, shell command or ingress write.
"""
# arming: agent-invoked; fixture-only until deployment mapping and ingress admission.
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from rdflib import Graph, Literal, RDF, RDFS, URIRef

from admit_infrastructure_output import CORE, DCT, INFRA, PROV, SCHEMA, admit


def iri(value):
    """Require an explicit absolute fixture/deployment identity, never a base guess."""
    if not isinstance(value, str) or not re.fullmatch(r"(?:https?://|urn:)[^\s<>\"{}|\\^`]+", value):
        raise ValueError("mapping requires an absolute IRI")
    return URIRef(value)


def terraform_resources(module):
    """Read managed resources in the standard terraform show -json values tree."""
    result = {}
    for resource in module.get("resources", []):
        if resource.get("mode") == "data":
            continue
        if resource.get("mode") != "managed":
            raise ValueError("unknown Terraform resource mode")
        address = resource["address"]
        if address in result:
            raise ValueError("duplicate Terraform resource address")
        result[address] = resource
    for child in module.get("child_modules", []):
        for address, resource in terraform_resources(child).items():
            if address in result:
                raise ValueError("duplicate Terraform resource address")
            result[address] = resource
    return result


def project(raw: bytes, mapping: dict):
    """Build the declared plane only; arbitrary source fields never enter RDF.

    Every input identity must have an explicit mapping or explicit exclusion.
    A caller's mapping is configuration, not identity adjudication authority.
    """
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError("input exceeds four MiB")
    source = json.loads(raw)
    producer = mapping["producer"]
    if producer == "ansible":
        resources = dict(source.get("_meta", {}).get("hostvars", {}))
        for name, group in source.items():
            if name == "_meta":
                continue
            for host in group.get("hosts", []):
                if not isinstance(host, str):
                    raise ValueError("inventory hostname must be a string")
                resources.setdefault(host, {})
        for host, variable in mapping.get("service_variables", {}).items():
            if host not in resources or not isinstance(variable, str):
                raise ValueError("service variable needs an existing inventory host")
            services = resources[host].get(variable, [])
            if not isinstance(services, list) or any(
                    not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@-]{0,127}", name)
                    for name in services):
                raise ValueError("declared services must be a list of unit names")
            if len(set(services)) != len(services):
                raise ValueError("duplicate declared service")
            for name in services:
                key = f"{host}/service/{name}"
                if key in resources:
                    raise ValueError("service identity collides with an inventory host")
                resources[key] = {}
    elif producer == "terraform":
        if str(source.get("format_version", "")).split(".")[0] != "1":
            raise ValueError("unsupported Terraform JSON format version")
        resources = terraform_resources(source["values"]["root_module"])
    else:
        raise ValueError("unknown producer")
    entities, excluded = mapping["entities"], mapping.get("excluded", {})
    if set(entities) & set(excluded) or set(resources) != set(entities) | set(excluded):
        raise ValueError("every exported identity needs exactly one mapping or exclusion")
    if any(not isinstance(reason, str) or not reason.strip() for reason in excluded.values()):
        raise ValueError("exclusion requires a reason")
    revision, extractor = mapping["source_revision"], mapping["extractor_revision"]
    if not all(isinstance(v, str) and re.fullmatch(r"[0-9a-f]{40}", v) for v in (revision, extractor)):
        raise ValueError("source and extractor need exact commit SHAs")
    graph, manifest = Graph(), {}
    source_iri, activity = iri(mapping["source_iri"]), iri(mapping["activity_iri"])
    graph.add((source_iri, RDF.type, PROV.Entity))
    graph.add((source_iri, DCT.identifier, Literal(revision)))
    graph.add((source_iri, CORE.contentSha256, Literal(hashlib.sha256(raw).hexdigest())))
    graph.add((source_iri, PROV.atLocation, iri(mapping["source_locator"])))
    graph.add((activity, RDF.type, PROV.Activity))
    graph.add((activity, DCT.identifier, Literal(extractor)))
    graph.add((activity, PROV.used, source_iri))
    allowed = {str(CORE.Host), str(CORE.BareMetalHost), str(CORE.ProxmoxNode),
               str(CORE.LXCContainer), str(CORE.Service), str(CORE.SystemdService)}
    entries = [(key, config, "declared", key) for key, config in sorted(entities.items())]
    state_subjects = mapping.get("state_subjects", {})
    if state_subjects and producer != "terraform":
        raise ValueError("state subjects require a Terraform values export")
    state_labels = {}
    for key, config in sorted(state_subjects.items()):
        if key in entities or config["resource"] not in entities:
            raise ValueError("state subject requires its separate mapped resource declaration")
        field = config["label_field"]
        if field not in {"hostname", "name", "node_name", "target_node"}:
            raise ValueError("state label field is not approved")
        label = resources[config["resource"]].get("values", {}).get(field)
        if not isinstance(label, str) or not label.strip() or len(label) > 253:
            raise ValueError("state subject label is missing or unsupported")
        state_labels[key] = label
        entries.append((key, config, "observed", label))
    all_entities = {**entities, **state_subjects}
    subjects = [iri(config["subject"]) for _, config, _, _ in entries]
    if len(set(subjects)) != len(subjects):
        raise ValueError("duplicate subject address across distinct mapping keys")
    for key, config, plane, label in entries:
        record, subject = iri(config["record"]), iri(config["subject"])
        kind = iri(config["type"])
        if (producer == "terraform" and plane == "declared" and kind != INFRA.TerraformResource
                or plane == "observed" and kind not in {CORE.Host, CORE.ProxmoxNode, CORE.LXCContainer}
                or producer == "ansible" and str(kind) not in allowed):
            raise ValueError("unsupported producer class")
        identifier = config["identifier"]
        if not isinstance(identifier, str) or not identifier.strip():
            raise ValueError("scoped identifier required")
        manifest[str(record)] = {"subject": str(subject), "identifier": identifier}
        graph.add((record, RDF.type, PROV.Entity))
        graph.add((record, DCT.conformsTo, INFRA.ConnectorRecordShape))
        graph.add((record, SCHEMA.about, subject))
        graph.add((record, CORE.sourceKind, Literal(plane)))
        # Digest of the approved projection, never arbitrary secret-bearing values.
        approved = {"mapping": config, "label": label, "plane": plane}
        digest = hashlib.sha256(json.dumps(approved, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        graph.add((record, CORE.contentSha256, Literal(digest)))
        graph.add((record, PROV.wasDerivedFrom, source_iri))
        graph.add((record, PROV.wasGeneratedBy, activity))
        graph.add((subject, RDF.type, kind))
        graph.add((subject, SCHEMA.identifier, Literal(identifier)))
        graph.add((subject, RDFS.label, Literal(label)))
        if kind in {CORE.LXCContainer, CORE.Service, CORE.SystemdService}:
            host = all_entities.get(config.get("host"))
            if host is None or host.get("type") not in {str(CORE.Host), str(CORE.ProxmoxNode)}:
                raise ValueError("guest requires an explicitly mapped host")
            if plane == "observed":
                field = config.get("host_field")
                if field not in {"node_name", "target_node"}:
                    raise ValueError("state guest needs an approved host field")
                actual = resources[config["resource"]].get("values", {}).get(field)
                if actual != state_labels.get(config["host"]):
                    raise ValueError("state guest placement contradicts the mapped host")
            graph.add((subject, CORE.runs_on, iri(host["subject"])))
    if len(manifest) != len(entries):
        raise ValueError("duplicate record address")
    admit(graph, manifest)
    return graph, manifest, {"mapped": len(entries), "excluded": excluded}


def read_bounded(path):
    with path.open("rb") as stream:
        data = stream.read(4 * 1024 * 1024 + 1)
    if len(data) > 4 * 1024 * 1024:
        raise ValueError("input exceeds four MiB")
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("mapping", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    raw = read_bounded(args.input)
    mapping = read_bounded(args.mapping)
    graph, manifest, report = project(raw, json.loads(mapping))
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "output.ttl").write_text(graph.serialize(format="turtle"))
    (args.output / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    (args.output / "report.json").write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")


if __name__ == "__main__":
    main()
