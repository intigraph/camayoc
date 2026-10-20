"""Build an offline, lossless source candidate; never load or rewrite a store."""
import argparse
import hashlib
import json
from pathlib import Path

from rdflib import Graph, RDF, SH, URIRef
from rdflib.compare import isomorphic, to_canonical_graph


def compatibility_bundle(root, source):
    """Validate all inputs before returning deterministic relative-path bytes."""
    root = Path(root)
    packs = root / "shapes/aegis"
    placement = json.loads((packs / "placement.json").read_text())
    original_bytes = Path(source).read_bytes()
    digest = hashlib.sha256(original_bytes).hexdigest()
    if digest != placement["source_sha256"]:
        raise ValueError("upstream source hash differs from pinned placement")
    original = Graph().parse(data=original_bytes, format="turtle")
    rows = placement["shapes"]
    assigned = [row["shape"] for row in rows]
    expected = {str(s) for s in original.subjects(RDF.type, SH.NodeShape)}
    if len(assigned) != len(set(assigned)) or set(assigned) != expected:
        raise ValueError("shape assignment is duplicated or incomplete")

    retained = "tests/fixtures/aegis-upstream/kept-governance-compatibility.shapes.ttl"
    destinations = {
        "shapes/aegis/homelab-infra.shapes.ttl": "homelab-infra.compatibility.ttl",
        "shapes/aegis/personal-media.shapes.ttl": "personal-media.compatibility.ttl",
        "shapes/aegis/software-factory.shapes.ttl": "software-factory.compatibility.ttl",
        "shapes/aegis/public-term-profiles.shapes.ttl": "public-term-profiles.compatibility.ttl",
        retained: "engine-retained.compatibility.ttl",
    }
    if set(row["file"] for row in rows) != set(destinations):
        raise ValueError("unreviewed or missing source pack path")

    combined = Graph()
    artifacts = {}
    records = []
    for file, output in sorted(destinations.items()):
        graph = Graph().parse(root / file)
        subset = [row for row in rows if row["file"] == file]
        actual = {str(s) for s in graph.subjects(RDF.type, SH.NodeShape)}
        if actual != {row["shape"] for row in subset}:
            raise ValueError("source pack shape coverage differs from placement")
        for row in subset:
            shape = URIRef(row["shape"])
            old_targets = set(row["source_targets"])
            substitutions = row["public_target_substitutions"]
            if not set(substitutions).issubset(old_targets):
                raise ValueError("substitution lacks an original target")
            effective = {substitutions.get(target, target) for target in old_targets}
            if {str(t) for t in graph.objects(shape, SH.targetClass)} != effective:
                raise ValueError("source pack targets differ from placement")
            for target in list(graph.objects(shape, SH.targetClass)):
                graph.remove((shape, SH.targetClass, target))
            for target in sorted(old_targets):
                graph.add((shape, SH.targetClass, URIRef(target)))
        combined += graph
        artifacts[output] = canonical_bytes(graph)
        records.append({"path": output, "role": "engine-retained-candidate" if file == retained
                        else "domain-compatibility-candidate", "node_shapes": len(actual)})

    vocabulary = Graph().parse(packs / "shared-vocabulary.ttl")
    combined += vocabulary
    artifacts["shared-vocabulary.ttl"] = canonical_bytes(vocabulary)
    records.append({"path": "shared-vocabulary.ttl", "role": "shared-declarations-candidate",
                    "node_shapes": 0})
    if not isomorphic(combined, original):
        raise ValueError("recombined constraints are not RDF-isomorphic to pinned source")

    # Check the serialized files too: independent parsing must not lose or fuse blank nodes.
    decoded = Graph()
    for content in artifacts.values():
        decoded += Graph().parse(data=content, format="turtle")
    if not isomorphic(decoded, original):
        raise ValueError("serialized bundle does not preserve the source graph")
    for record in records:
        content = artifacts[record["path"]]
        record.update(sha256=hashlib.sha256(content).hexdigest(), bytes=len(content))
    manifest = {"format": "aegis-compatibility-source/v1", "automatic_load": False,
                "source_repository": placement["source_repository"],
                "source_commit": placement["source_commit"], "source_sha256": digest,
                "node_shapes": len(expected), "union_isomorphic": True,
                "files": records,
                "limits": "Source equivalence only; no loaded-set, inference, alias-query or live-data acceptance."}
    artifacts["manifest.json"] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    return artifacts


def canonical_bytes(graph):
    """Sorted canonical N-Triples is also Turtle; no parser-order blank-node IDs."""
    canonical = to_canonical_graph(graph)
    return ("\n".join(sorted(" ".join(term.n3() for term in triple) + " ."
                             for triple in canonical)) + "\n").encode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path,
                        help="pinned upstream ontology file; checked by SHA256")
    parser.add_argument("--output", required=True, type=Path,
                        help="NEW directory; existing directories are refused")
    args = parser.parse_args()
    try:
        artifacts = compatibility_bundle(Path(__file__).resolve().parents[1], args.source)
        args.output.mkdir(mode=0o700)  # atomic refusal if another writer created it
        for name, content in artifacts.items():
            with (args.output / name).open("xb") as handle:
                handle.write(content)
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(2, f"compatibility bundle refused: {exc}\n")
    print(artifacts["manifest.json"].decode(), end="")


if __name__ == "__main__":
    main()
