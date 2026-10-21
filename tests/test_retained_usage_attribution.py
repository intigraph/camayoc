"""Native validation preserves stored attribution while new writes use Seeds."""
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from rdflib import Graph, SH, URIRef
from quipu_bin_guard import QUIPU, requires_quipu

ROOT = Path(__file__).resolve().parents[1]
NS = 'http://aegis.gastown.local/ontology/'
spec = importlib.util.spec_from_file_location('seed_profile', ROOT/'scripts/seed_profile.py')
profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)
PREFIX = '''@prefix a: <http://aegis.gastown.local/ontology/> .
@prefix x: <https://example.org/retained-usage/> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix dct: <http://purl.org/dc/terms/> .
@prefix q: <https://scbrown.github.io/quechua/ns#> .
@prefix schema: <https://schema.org/> .
@prefix seeds: <https://seeds.local/ontology/> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
'''
USAGE = '''x:session a a:Session; rdfs:label "session";
 a:sourceKind "observed"; a:actor x:actor .
x:usage a a:UsageRecord; rdfs:label "usage"; a:sourceKind "observed";
 a:inSession x:session; a:provider "fixture"; a:tokensConsumed 1;
 a:observedAt "2026-10-20T00:00:00Z"; a:attributedTo x:work .
x:work rdfs:label "work"; a:sourceKind "observed" .
'''


@requires_quipu
class RetainedUsage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        source = Path(QUIPU).resolve()
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        cls.binary = cls.root/'quipu'
        shutil.copyfile(source, cls.binary); cls.binary.chmod(0o700)
        if hashlib.sha256(cls.binary.read_bytes()).hexdigest() != digest:
            raise RuntimeError('private validation artifact changed during copy')
        print('Usage attribution validator sha256='+digest)
        cls.core = Graph().parse(ROOT/'shapes/core.shapes.ttl')
        cls.context = Graph().parse(ROOT/'ontology/core.ttl')
        for _, turtle in profile.profiles(source=os.environ.get('CAMAYOC_SEEDS_SOURCE')):
            cls.context += Graph().parse(data=turtle, format='turtle')

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def validate(self, target, *, historical_contract=False):
        shapes = Graph() + self.core
        if historical_contract:
            # Restore ONLY the previous attribution property contract, keeping
            # the complete pinned Seeds/ontology context and all other shapes.
            for prop in shapes.objects(URIRef(NS+'CamayocUsageRecordShape'), SH.property):
                if (prop, SH.path, URIRef(NS+'attributedTo')) in shapes:
                    shapes.remove((prop, SH['or'], None))
                    shapes.remove((prop, SH['class'], None))
                    shapes.add((prop, SH['class'], URIRef(NS+'WorkItem')))
        shapes += self.context
        shape_file = self.root/'shapes.ttl'; data_file = self.root/'data.ttl'
        shape_file.write_text(shapes.serialize(format='turtle'))
        data_file.write_text(PREFIX+USAGE+target)
        return subprocess.run([str(self.binary), 'validate', '--shapes', str(shape_file),
                               '--data', str(data_file)], capture_output=True, text=True,
                              timeout=20, check=False)

    def test_retained_workitem_reference_stays_valid(self):
        target = 'x:work a a:WorkItem; dct:isReplacedBy schema:Action .'
        baseline = self.validate(target, historical_contract=True)
        self.assertEqual(baseline.returncode, 0, baseline.stdout+baseline.stderr)
        current = self.validate(target)
        self.assertEqual(current.returncode, 0, current.stdout+current.stderr)

    def test_canonical_seeds_action_reference_stays_valid(self):
        result = self.validate('''x:work a schema:Action; schema:name "work";
 schema:identifier "fixture"; schema:version 1; schema:actionStatus schema:PotentialActionStatus;
 schema:dateCreated "2026-10-20T00:00:00Z"^^xsd:dateTime; q:sourceKind "declared";
 seeds:status "open"; seeds:priority 2; seeds:issueType "task" .''')
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)

    def test_unrelated_and_untyped_targets_remain_invalid(self):
        for target in ('x:work a x:Unrelated .', ''):
            with self.subTest(target=target):
                result = self.validate(target)
                self.assertEqual(result.returncode, 1, result.stdout+result.stderr)
                self.assertIn('attributedTo', result.stdout+result.stderr)


if __name__ == '__main__': unittest.main()
