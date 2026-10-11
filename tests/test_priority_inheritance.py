"""Native rule withdrawal and the query's explicit dataset contract."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from quipu_bin_guard import QUIPU, requires_quipu

ROOT = Path(__file__).resolve().parents[1]
PREFIX = '''@prefix schema: <https://schema.org/> .
@prefix seeds: <https://seeds.local/ontology/> .
@prefix q: <https://scbrown.github.io/quechua/ns#> .
@prefix ex: <https://example.org/> .
'''
ACTION = 'a schema:Action ; schema:actionStatus schema:PotentialActionStatus'


@requires_quipu
class PriorityRules(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = Path(self.directory.name) / 'fixture.db'

    def run_cli(self, *args):
        result = subprocess.run([str(QUIPU), *args, '--db', str(self.db)],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def seed(self, body):
        source = Path(self.directory.name) / 'fixture.ttl'
        source.write_text(PREFIX + body)
        self.run_cli('knot', str(source))

    def reason(self):
        self.run_cli('reason', '--rules', str(ROOT / 'rules/priority-inheritance.ttl'))

    def scalar(self, query):
        lines = self.run_cli('read', query).splitlines()
        return int(lines[lines.index('--------------------') + 1])

    def root_gates(self):
        return self.scalar('SELECT (COUNT(?gate) AS ?n) WHERE { GRAPH '
                           '<urn:quipu:graph:root#inferred> { ?gate '
                           '<https://seeds.local/ontology/activeBlocks> '
                           '<https://example.org/root> } }')

    def priority(self):
        record = json.loads((ROOT / 'queries/camayoc_effective_work_priority.json').read_text())
        query = record['template'].replace('{project}', 'urn:quipu:graph:root')
        query = query.replace('{inferred}', 'urn:quipu:graph:root#inferred')
        rows = self.run_cli('read', query)
        self.assertIn('effectivePriority', rows)
        return {line.split('\t')[0]: int(line.split('\t')[1])
                for line in rows.splitlines() if line.startswith('https://example.org/') and '\t' in line}

    def test_direct_and_chain_derive_and_close_withdraws(self):
        self.seed(f'ex:gate {ACTION} ; seeds:priority 3 . '
                  f'ex:middle {ACTION} ; seeds:priority 4 ; q:blockedOn ex:gate . '
                  f'ex:root {ACTION} ; seeds:priority 0 ; q:blockedOn ex:middle .')
        self.reason()
        self.assertEqual(self.root_gates(), 2)
        self.assertEqual(self.priority()['https://example.org/gate'], 0)
        self.run_cli('retract', 'https://example.org/root', '--predicate',
                     'https://schema.org/actionStatus')
        self.seed('ex:root schema:actionStatus schema:CompletedActionStatus .')
        self.reason()
        self.assertEqual(self.root_gates(), 0)
        self.assertEqual(self.priority()['https://example.org/gate'], 3)

    def test_related_and_non_work_entity_do_not_inherit(self):
        self.seed(f'ex:gate {ACTION} ; seeds:priority 3 . '
                  f'ex:root {ACTION} ; seeds:priority 0 ; '
                  '<http://purl.org/dc/terms/relation> ex:gate . '
                  'ex:notwork a schema:Event ; schema:actionStatus '
                  'schema:PotentialActionStatus ; q:blockedOn ex:gate .')
        self.reason()
        self.assertEqual(self.root_gates(), 0)

    def test_edge_removal_withdraws(self):
        self.seed(f'ex:gate {ACTION} ; seeds:priority 3 . '
                  f'ex:root {ACTION} ; seeds:priority 0 ; q:blockedOn ex:gate .')
        self.reason()
        self.assertEqual(self.root_gates(), 1)
        self.run_cli('retract', 'https://example.org/root', '--predicate',
                     'https://scbrown.github.io/quechua/ns#blockedOn')
        self.reason()
        self.assertEqual(self.root_gates(), 0)


    def test_cycle_without_active_p0_does_not_retain_p0_rank(self):
        self.seed(f'ex:gate {ACTION} ; seeds:priority 3 ; q:blockedOn ex:middle . '
                  f'ex:middle {ACTION} ; seeds:priority 4 ; q:blockedOn ex:gate . '
                  f'ex:root {ACTION} ; seeds:priority 0 ; q:blockedOn ex:gate .')
        self.reason()
        self.assertEqual(self.priority()['https://example.org/gate'], 0)
        self.run_cli('retract', 'https://example.org/root', '--predicate',
                     'https://schema.org/actionStatus')
        self.seed('ex:root schema:actionStatus schema:CompletedActionStatus .')
        self.reason()
        self.assertEqual(self.root_gates(), 0)
        self.assertEqual(self.priority()['https://example.org/gate'], 3)
        self.assertGreater(self.scalar('SELECT (COUNT(?gate) AS ?n) WHERE { GRAPH '
                                      '<urn:quipu:graph:root#inferred> { ?gate '
                                      '<https://seeds.local/ontology/activeBlocks> ?root } }'), 0)


if __name__ == '__main__':
    unittest.main()
