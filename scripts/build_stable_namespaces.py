"""Build additive Intigraph namespace candidates; never load or rewrite a store."""
import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from rdflib import Graph, OWL, RDF, RDFS, SH, URIRef
from rdflib.compare import isomorphic, to_canonical_graph

from build_aegis_compatibility import canonical_bytes

APPROVED_LEDGER_SHA256 = 'b8223fa1af79495547e5032c1e58edd3c46665dd920401cb81384f41597dc924'
# Stage two disposes the 39 retained terms plus 8 NodeShapes the stage-one guard
# could not see (aegis-m5s.1.1). Its pairs are accepted but not yet published.
APPROVED_STAGE_TWO_SHA256 = '5a3007a3b28300d85decec78b8e3629c1f33d1b9ea07d97521bf48b3016a0d71'
LEGACY_NAMESPACES = ('http://aegis.gastown.local/ontology/', 'https://camayoc.local/ontology/')
# Every named declaration kind a stable file can carry, shapes included.
DECLARATION_KINDS = (RDF.Property, RDFS.Class, OWL.Class, OWL.ObjectProperty,
                     OWL.DatatypeProperty, SH.NodeShape)

SOURCES = {
    'core': ('shapes/core.shapes.ttl',),
    'delivery': ('shapes/delivery.shapes.ttl',),
    'code-entities': ('shapes/code-entities.ttl',),
    'session-transcript': ('shapes/session-transcript.shapes.ttl',),
    'usage-breakdown': ('shapes/usage-breakdown.shapes.ttl',),
    'homelab-infra': ('shapes/aegis/homelab-infra.shapes.ttl',),
    'personal-media': ('shapes/aegis/personal-media.shapes.ttl',),
    'software-factory': ('shapes/aegis/software-factory.shapes.ttl',),
}
ENGINE_ANNOTATIONS = {URIRef('http://quipu.dev/ontology/onViolation'),
                      URIRef('http://quipu.dev/ns#onViolation')}
EXECUTABLE_TEXT = {SH.select, SH.ask, SH.construct, SH.js, SH.jsFunctionName}


def mapping_ledger(path):
    """Accept exactly the measured stage-one bijection, retaining stage two."""
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != APPROVED_LEDGER_SHA256:
        raise ValueError('mapping differs from the frozen stage-one snapshot')
    ledger = json.loads(raw)
    if ledger.get('format') != 'intigraph-namespace-map/v1':
        raise ValueError('unsupported mapping format')
    rows = ledger['pairs']
    if ledger.get('pair_count') != 1254 or len(rows) != 1254:
        raise ValueError('stage one requires exactly 1254 measured pairs')
    old = [row['old'] for row in rows]
    new = [row['new'] for row in rows]
    if len(set(old)) != len(rows) or len(set(new)) != len(rows):
        raise ValueError('mapping is not one-to-one')
    retained = ledger['retained_legacy']
    if len(retained) != 39 or len(set(retained)) != 39 or set(old) & set(retained):
        raise ValueError('the 39 stage-two identifiers must remain legacy')
    for row in rows:
        if not urlsplit(row['old']).scheme or any(c.isspace() for c in row['old']):
            raise ValueError('old identifier must be an absolute IRI')
        if not row['new'].startswith('https://w3id.org/intigraph/'):
            raise ValueError('destination is outside the registered namespace')
        if not set(row['roles']) <= {'class', 'property'}:
            raise ValueError('unrecognized semantic role')
    return ledger


def stage_two_ledger(path, base):
    """Accept exactly the reviewed stage-two dispositions on top of stage one."""
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != APPROVED_STAGE_TWO_SHA256:
        raise ValueError('stage-two mapping differs from the reviewed snapshot')
    ledger = json.loads(raw)
    if (ledger.get('format') != 'intigraph-namespace-map/stage-two-v1'
            or ledger.get('base_mapping_sha256') != APPROVED_LEDGER_SHA256):
        raise ValueError('stage two does not extend the frozen stage-one snapshot')
    rows = ledger['pairs']
    if ledger.get('pair_count') != 45 or len(rows) != 45:
        raise ValueError('stage two requires exactly 45 reviewed pairs')
    old = {row['old'] for row in rows}
    new = {row['new'] for row in rows}
    if len(old) != len(rows) or len(new) != len(rows):
        raise ValueError('stage-two mapping is not one-to-one')
    if old & {row['old'] for row in base['pairs']} or new & {row['new'] for row in base['pairs']}:
        raise ValueError('stage two collides with a stage-one pair')
    retained = set(ledger['retained_legacy'])
    if len(retained) != 2 or old & retained:
        raise ValueError('stage two keeps exactly two legacy identifiers')
    # Every stage-one retained term gets exactly one disposition.
    if set(base['retained_legacy']) != (old & set(base['retained_legacy'])) | retained:
        raise ValueError('a stage-one retained term has no stage-two disposition')
    for row in rows:
        if not row['old'].startswith(LEGACY_NAMESPACES):
            raise ValueError('stage two only migrates legacy declarations')
        prefix, _, local = row['new'].partition('/ns#')
        if (not prefix.startswith('https://w3id.org/intigraph/camayoc/')
                or local != row['old'].rsplit('/', 1)[1]):
            raise ValueError('stage two keeps each local name in a Camayoc pack')
        if not set(row['roles']) <= {'class', 'property'}:
            raise ValueError('unrecognized semantic role')
    return ledger


def combined_ledger(root, ledger_path=None, stage_two_path=None):
    """Stage one plus stage two, with the unpublished stage-two pairs named."""
    root = Path(root)
    base = mapping_ledger(ledger_path or root / 'namespaces/intigraph/mapping.json')
    stage_two = stage_two_ledger(stage_two_path or root / 'namespaces/intigraph/stage-two.json',
                                 base)
    moved = [row['old'] for row in stage_two['pairs']]
    return {'pairs': base['pairs'] + stage_two['pairs'],
            'retained_legacy': stage_two['retained_legacy'],
            'stage_two': moved, 'unpublished': [] if stage_two['published'] else moved}


def remap_graph(graph, mapping):
    """Map exact RDF IRIs; preserve blank nodes and literal lexical forms/types."""
    result = Graph()
    for triple in graph:
        if triple[1] in EXECUTABLE_TEXT:
            raise ValueError('executable text requires an explicit query migration')
        mapped = tuple(URIRef(mapping.get(str(term), str(term)))
                       if isinstance(term, URIRef) else term for term in triple)
        result.add(mapped)
        # The released parser recognizes these legacy policy predicates directly,
        # before ontology reasoning. Preserve their effect alongside new IRIs.
        if triple[1] in ENGINE_ANNOTATIONS:
            result.add((mapped[0], triple[1], mapped[2]))
    return result


def shape_bytes(graph, ledger):
    """Keep each named shape and its policy in ONE Turtle statement block."""
    output = Graph()
    output.bind('sh', SH)
    output.bind('aegis', 'http://aegis.gastown.local/ontology/')
    output.bind('camayoc', 'https://camayoc.local/ontology/')
    output.bind('quipu', 'http://quipu.dev/ontology/')
    namespaces = sorted({row['new'].rsplit('#', 1)[0] + '#'
                         for row in ledger['pairs'] if '#' in row['new']})
    for index, namespace in enumerate(namespaces):
        output.bind(f'stable_{index}', namespace)
    for triple in to_canonical_graph(graph):
        output.add(triple)
    return output.serialize(format='turtle').encode()


def alias_graph(ledger):
    """Identity alone cannot bridge a predicate in the supported Quipu engine."""
    graph = Graph()
    for row in ledger['pairs']:
        old, new = URIRef(row['old']), URIRef(row['new'])
        graph.add((old, OWL.sameAs, new))
        if 'property' in row['roles']:
            graph.add((old, OWL.equivalentProperty, new))
        if 'class' in row['roles']:
            graph.add((old, OWL.equivalentClass, new))
    return graph


def stable_bundle(root, ledger_path=None, stage_two_path=None):
    root = Path(root)
    ledger_path = Path(ledger_path or root / 'namespaces/intigraph/mapping.json')
    stage_two_path = Path(stage_two_path or root / 'namespaces/intigraph/stage-two.json')
    ledger = combined_ledger(root, ledger_path, stage_two_path)
    mapping = {row['old']: row['new'] for row in ledger['pairs']}
    inverse = {new: old for old, new in mapping.items()}
    artifacts, records, subjects = {}, [], set()
    # Ontology facts and SHACL gates retain distinct loading roles.
    groups = {'camayoc/core/ontology.ttl': ('ontology/core.ttl',)}
    groups.update({f'camayoc/{pack}/ns.ttl': files for pack, files in SOURCES.items()})
    # Keep debt in its own file, matching the existing reject/emit separation.
    groups['camayoc/core/observation-debt.ttl'] = ('shapes/observation-debt.shapes.ttl',)
    groups['compatibility/shared-vocabulary.ttl'] = ('shapes/aegis/shared-vocabulary.ttl',)
    for output, sources in groups.items():
        original = Graph()
        inputs = []
        for source in sources:
            data = (root / source).read_bytes()
            original += Graph().parse(data=data, format='turtle')
            inputs.append({'path': source, 'sha256': hashlib.sha256(data).hexdigest()})
        declarations = {str(subject) for kind in DECLARATION_KINDS
                        for subject in original.subjects(RDF.type, kind)}
        subjects |= declarations
        unmapped = {term for term in declarations
                    if term.startswith(LEGACY_NAMESPACES) and term not in mapping}
        if not unmapped <= set(ledger['retained_legacy']):
            raise ValueError('new unmapped declaration requires a separate disposition')
        migrated = remap_graph(original, mapping)
        if not isomorphic(remap_graph(migrated, inverse), original):
            raise ValueError('inverse mapping does not preserve source constraints')
        # A declared predicate/path must never fall back to sameAs-only aliasing.
        used = {str(pred) for _, pred, _ in original}
        used |= {str(value) for value in original.objects(None, SH.path)
                 if isinstance(value, URIRef)}
        property_roles = {row['old'] for row in ledger['pairs']
                          if 'property' in row['roles']}
        if (used & mapping.keys()) - property_roles:
            raise ValueError('mapped property is missing equivalentProperty role')
        data = shape_bytes(migrated, ledger)
        if not isomorphic(Graph().parse(data=data, format='turtle'), migrated):
            raise ValueError('serialization changed the migrated graph')
        artifacts[output] = data
        records.append({'path': output, 'sources': inputs,
                        'node_shapes': len(set(original.subjects(RDF.type, SH.NodeShape))),
                        'source_triples': len(original), 'stable_triples': len(migrated),
                        'retained_engine_annotations': sum(p in ENGINE_ANNOTATIONS for _, p, _ in original),
                        'inverse_isomorphic': True})
    # A stage-two pair must name something these sources actually declare.
    if not set(ledger['stage_two']) <= subjects:
        raise ValueError('stage-two pair names no declared source term')
    artifacts['compatibility/aliases.ttl'] = canonical_bytes(alias_graph(ledger))
    for record in records:
        data = artifacts[record['path']]
        record.update(sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))
    manifest = {
        'format': 'intigraph-stable-source/v1', 'automatic_load': False,
        'mapping_sha256': hashlib.sha256(ledger_path.read_bytes()).hexdigest(),
        'stage_two_sha256': hashlib.sha256(stage_two_path.read_bytes()).hexdigest(),
        'pair_count': len(mapping), 'retained_legacy': ledger['retained_legacy'],
        'unpublished_pairs': len(ledger['unpublished']),
        'files': records,
        'aliases': {'path': 'compatibility/aliases.ttl',
                    'sha256': hashlib.sha256(artifacts['compatibility/aliases.ttl']).hexdigest()},
        'limits': ('Additive source candidate only. Retain legacy files and loaded sets. '
                   'Consumer admission timing, queries, full-data validation and '
                   'installation require separate acceptance; no automatic activation.'),
    }
    artifacts['manifest.json'] = (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode()
    return artifacts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    artifacts = stable_bundle(Path(__file__).resolve().parents[1])
    # Validate everything before creating the destination; never overwrite a bundle.
    args.output.mkdir(parents=True, exist_ok=False)
    for relative, data in artifacts.items():
        destination = args.output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    print(f'Built {len(artifacts)} source files; no store or network was touched')


if __name__ == '__main__':
    main()
