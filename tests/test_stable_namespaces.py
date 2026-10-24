"""Exact namespace migration preserves old source and every constraint graph."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from rdflib import Graph, Literal, OWL, RDF, SH, URIRef
from rdflib.compare import isomorphic

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import build_stable_namespaces
from build_stable_namespaces import (alias_graph, combined_ledger, mapping_ledger,
                                     remap_graph, SOURCES, stable_bundle)

AEGIS = 'http://aegis.gastown.local/ontology/'
KEPT = {AEGIS + 'closedAt', AEGIS + 'assignedTo'}
# The 8 NodeShapes the stage-one guard could not see, with the pack each moves to.
UNSEEN_SHAPES = {
    'ChangeEventShape': 'delivery', 'DeploymentEventIncludesShape': 'delivery',
    'DocumentVocabularyShape': 'code-entities',
    'CodeEntityLabelMaxCountDebtShape': 'code-entities',
    'SectionHeadingMaxCountDebtShape': 'code-entities',
    'CommitRequiredMetadataDebtShape': 'code-entities',
    'CodeModuleFilePathDebtShape': 'code-entities',
    'CamayocObservationProvenanceDebtShape': 'core'}


class StableNamespaces(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stage_one = mapping_ledger(ROOT / 'namespaces/intigraph/mapping.json')
        cls.ledger = combined_ledger(ROOT)
        cls.mapping = {r['old']: r['new'] for r in cls.ledger['pairs']}
        cls.inverse = {v: k for k, v in cls.mapping.items()}
        cls.artifacts = stable_bundle(ROOT)

    def test_all_eight_packs_preserve_constraints_and_legacy_bytes(self):
        before = {f: (ROOT/f).read_bytes() for files in SOURCES.values() for f in files}
        manifest = json.loads(self.artifacts['manifest.json'])
        self.assertFalse(manifest['automatic_load'])
        self.assertEqual(set(manifest['retained_legacy']), KEPT)
        self.assertEqual(manifest['pair_count'], 1254 + 45)
        self.assertEqual(manifest['unpublished_pairs'], 45)
        self.assertFalse(set(manifest['retained_legacy']) & self.mapping.keys())
        self.assertEqual(set(SOURCES), {'core', 'delivery', 'code-entities',
                         'session-transcript', 'usage-breakdown', 'homelab-infra',
                         'personal-media', 'software-factory'})
        for pack, files in SOURCES.items():
            original = Graph()
            for file in files:
                original += Graph().parse(ROOT/file)
            actual = Graph().parse(data=self.artifacts[f'camayoc/{pack}/ns.ttl'], format='turtle')
            self.assertTrue(isomorphic(remap_graph(actual, self.inverse), original), pack)
            self.assertFalse(isomorphic(Graph(), original), pack)
            self.assertEqual(len(set(actual.subjects(RDF.type, SH.NodeShape))),
                             len(set(original.subjects(RDF.type, SH.NodeShape))), pack)
        self.assertEqual(before, {f: (ROOT/f).read_bytes() for f in before})

    def test_every_property_has_a_real_property_equivalence_bridge(self):
        graph = Graph().parse(data=self.artifacts['compatibility/aliases.ttl'], format='turtle')
        self.assertEqual(len(list(graph.triples((None, OWL.sameAs, None)))), 1254 + 45)
        for row in self.ledger['pairs']:
            pair = URIRef(row['old']), URIRef(row['new'])
            self.assertIn((pair[0], OWL.sameAs, pair[1]), graph)
            self.assertEqual((pair[0], OWL.equivalentProperty, pair[1]) in graph,
                             'property' in row['roles'])
            self.assertEqual((pair[0], OWL.equivalentClass, pair[1]) in graph,
                             'class' in row['roles'])
        broken = Graph()
        for triple in graph:
            if triple[1] != OWL.equivalentProperty:
                broken.add(triple)
        self.assertNotEqual(set(broken), set(graph))
        self.assertEqual(len(list(broken.triples((None, OWL.equivalentProperty, None)))), 0)

    def test_emit_policy_keeps_the_released_engine_annotation(self):
        old = URIRef('http://quipu.dev/ontology/onViolation')
        new = URIRef(self.mapping[str(old)])
        found = 0
        for record in json.loads(self.artifacts['manifest.json'])['files']:
            actual = Graph().parse(data=self.artifacts[record['path']], format='turtle')
            for source in record['sources']:
                file = source['path']
                for shape, _, value in Graph().parse(ROOT/file).triples((None, old, None)):
                    shape = URIRef(self.mapping.get(str(shape), str(shape)))
                    self.assertIn((shape, old, value), actual)
                    self.assertIn((shape, new, value), actual)
                    found += 1
        self.assertEqual(found, 21)

    def test_exact_iri_mapping_never_rewrites_literals_or_prefix_neighbors(self):
        a, b = URIRef('https://example.org/old#p'), URIRef('https://example.org/new#p')
        neighbor = URIRef(str(a)+'Extra')
        literal = Literal(str(a), lang='en')
        typed = Literal('value', datatype=a)
        graph = Graph()
        graph.add((neighbor, a, literal))
        graph.add((a, neighbor, typed))
        actual = remap_graph(graph, {str(a): str(b)})
        self.assertIn((neighbor, b, literal), actual)
        self.assertIn((b, neighbor, typed), actual)
        self.assertTrue(isomorphic(remap_graph(actual, {str(b): str(a)}), graph))

    def test_executable_queries_refuse_instead_of_becoming_stale_constraints(self):
        graph = Graph()
        graph.add((URIRef('https://example.org/shape'), SH.select,
                   Literal('SELECT ?s WHERE { ?s a <https://example.org/old> }')))
        with self.assertRaisesRegex(ValueError, 'executable text'):
            remap_graph(graph, {})

    def test_modified_or_incomplete_mapping_refuses_before_output(self):
        for kind in ['missing', 'duplicate', 'redirected', 'stage-two', 'property-role']:
            ledger = copy.deepcopy(self.ledger)
            if kind == 'missing': ledger['pairs'].pop()
            elif kind == 'duplicate': ledger['pairs'][1] = ledger['pairs'][0]
            elif kind == 'redirected': ledger['pairs'][0]['new'] += 'wrong'
            elif kind == 'stage-two': ledger['pairs'][0]['old'] = ledger['retained_legacy'][0]
            else:
                row = next(r for r in ledger['pairs'] if 'property' in r['roles'])
                row['roles'].remove('property')
            with self.subTest(mutation=kind), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp)/'mapping.json'
                path.write_text(json.dumps(ledger))
                with self.assertRaisesRegex(ValueError, 'snapshot'):
                    stable_bundle(ROOT, path)
                self.assertEqual(list(Path(tmp).iterdir()), [path])

    def test_new_unmapped_declarations_refuse_instead_of_extending_stage_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for file in ('ontology/core.ttl', *SOURCES['core'], 'namespaces/intigraph/mapping.json',
                         'namespaces/intigraph/stage-two.json'):
                path = root/file
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((ROOT/file).read_bytes())
            with (root/'ontology/core.ttl').open('a') as stream:
                stream.write('\n<http://aegis.gastown.local/ontology/UnmappedControl> '
                             'a <http://www.w3.org/1999/02/22-rdf-syntax-ns#Property> .\n')
            with self.assertRaisesRegex(ValueError, 'new unmapped declaration'):
                stable_bundle(root)

    def test_unmapped_node_shape_refuses_like_any_other_declaration(self):
        # Stage one only checked properties and classes, so 8 shapes slipped through.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for file in ('ontology/core.ttl', 'namespaces/intigraph/mapping.json',
                         'namespaces/intigraph/stage-two.json'):
                path = root/file
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((ROOT/file).read_bytes())
            with (root/'ontology/core.ttl').open('a') as stream:
                stream.write('\n<http://aegis.gastown.local/ontology/UnmappedShapeControl> '
                             'a <http://www.w3.org/ns/shacl#NodeShape> .\n')
            with self.assertRaisesRegex(ValueError, 'new unmapped declaration'):
                stable_bundle(root)

    def test_stage_two_disposes_every_retained_term_and_unseen_shape(self):
        moved = {row['old']: row for row in self.ledger['pairs'][1254:]}
        self.assertEqual(set(moved) | KEPT, set(self.stage_one['retained_legacy'])
                         | {AEGIS + local for local in UNSEEN_SHAPES})
        self.assertFalse(set(moved) & KEPT)
        self.assertEqual(set(self.ledger['retained_legacy']), KEPT)
        for local, pack in UNSEEN_SHAPES.items():
            row = moved[AEGIS + local]
            self.assertEqual(row['new'], f'https://w3id.org/intigraph/camayoc/{pack}/ns#{local}')
            self.assertEqual(row['roles'], [])
        for old, row in moved.items():
            self.assertEqual(row['new'].rsplit('#', 1)[1], old.rsplit('/', 1)[1])

    def test_no_legacy_declaration_survives_in_a_stable_file_except_the_kept_two(self):
        kinds = build_stable_namespaces.DECLARATION_KINDS
        legacy = build_stable_namespaces.LEGACY_NAMESPACES
        seen_in_sources = set()
        for record in json.loads(self.artifacts['manifest.json'])['files']:
            original = Graph()
            for source in record['sources']:
                original += Graph().parse(ROOT/source['path'])
            actual = Graph().parse(data=self.artifacts[record['path']], format='turtle')
            declared = lambda graph: {str(s) for kind in kinds for s in graph.subjects(RDF.type, kind)
                                      if str(s).startswith(legacy)}
            seen_in_sources |= declared(original)
            self.assertLessEqual(declared(actual), KEPT, record['path'])
        # Positive control: the sources really did declare the kept and moved terms.
        self.assertLessEqual(KEPT | {AEGIS + local for local in UNSEEN_SHAPES}, seen_in_sources)

    def test_stage_two_semantic_checks_refuse_each_bad_disposition(self):
        stage_two = json.loads((ROOT/'namespaces/intigraph/stage-two.json').read_text())
        shape = next(r for r in stage_two['pairs'] if r['old'].endswith('ChangeEventShape'))
        first_v1 = self.stage_one['pairs'][0]
        cases = {
            'renamed': ('local name', lambda d: shape.update(new=shape['new'] + 'Renamed')),
            'foreign-pack': ('local name', lambda d: shape.update(
                new=shape['new'].replace('/camayoc/delivery/', '/quipu/'))),
            'collision': ('collides', lambda d: shape.update(new=first_v1['new'])),
            'undisposed': ('no stage-two disposition', lambda d: d['retained_legacy'].__setitem__(
                0, AEGIS + 'NeverRetainedControl')),
        }
        for name, (message, mutate) in cases.items():
            data = copy.deepcopy(stage_two)
            shape = next(r for r in data['pairs'] if r['old'].endswith('ChangeEventShape'))
            mutate(data)
            with self.subTest(mutation=name), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp)/'stage-two.json'
                raw = (json.dumps(data, indent=2) + '\n').encode()
                path.write_bytes(raw)
                with self.assertRaisesRegex(ValueError, 'reviewed snapshot'):
                    stable_bundle(ROOT, stage_two_path=path)
                # Past the hash pin, the semantic check still names the defect.
                pinned = build_stable_namespaces.APPROVED_STAGE_TWO_SHA256
                build_stable_namespaces.APPROVED_STAGE_TWO_SHA256 = hashlib.sha256(raw).hexdigest()
                try:
                    with self.assertRaisesRegex(ValueError, message):
                        stable_bundle(ROOT, stage_two_path=path)
                finally:
                    build_stable_namespaces.APPROVED_STAGE_TWO_SHA256 = pinned
                self.assertEqual(list(Path(tmp).iterdir()), [path])

    def test_stage_two_pair_for_a_vanished_declaration_refuses(self):
        # A source edit that drops a mapped shape's IRI leaves a stale pair behind.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for directory in ('ontology', 'shapes', 'namespaces'):
                shutil.copytree(ROOT/directory, root/directory)
            path = root/'shapes/delivery.shapes.ttl'
            text = path.read_text()
            self.assertEqual(text.count('aegis:ChangeEventShape a sh:NodeShape'), 1)
            path.write_text(text.replace('aegis:ChangeEventShape a sh:NodeShape', '[] a sh:NodeShape'))
            with self.assertRaisesRegex(ValueError, 'names no declared source term'):
                stable_bundle(root)

    def test_manifest_hashes_bind_every_source_and_output(self):
        manifest = json.loads(self.artifacts['manifest.json'])
        for record in manifest['files']:
            data = self.artifacts[record['path']]
            self.assertEqual(record['sha256'], hashlib.sha256(data).hexdigest())
            for source in record['sources']:
                self.assertEqual(source['sha256'], hashlib.sha256((ROOT/source['path']).read_bytes()).hexdigest())
        self.assertEqual(manifest['aliases']['sha256'], hashlib.sha256(
            self.artifacts[manifest['aliases']['path']]).hexdigest())

    def test_cli_preserves_existing_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'bundle'
            path.mkdir()
            (path/'keep').write_text('existing')
            result = subprocess.run([sys.executable, str(ROOT/'scripts/build_stable_namespaces.py'),
                                     '--output', str(path)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((path/'keep').read_text(), 'existing')
            self.assertEqual([p.name for p in path.iterdir()], ['keep'])


if __name__ == '__main__':
    unittest.main()
