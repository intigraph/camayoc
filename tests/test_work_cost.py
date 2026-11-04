import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from ingest_session_usage import read_codex, read_claude
from retrieve_metric import execute


def response(key, at, inp=100, cache=80, write=0, output=7):
    return {'type': 'token_usage_record', 'timestamp': at,
            'payload': {'session_id': 's', 'response_id': key, 'usage': {
                'input_tokens': inp, 'cached_input_tokens': cache,
                'cache_write_input_tokens': write, 'output_tokens': output}}}


class CostTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'session.jsonl'

    def write(self, rows):
        self.path.write_text(''.join(json.dumps(r) + '\n' for r in rows))

    def method(self, bindings):
        return {'system': 'session_usage', 'query': 'work_cost', 'params': {
            'sources': [{'path': str(self.path), 'harness': 'codex', 'agent': 'worker'}],
            'bindings': bindings}}

    def binding(self, bead, start, end):
        return {'schema_version': 1, 'rig': 'project', 'bead': bead,
                'agent': 'worker', 'session': 's', 'start': start, 'end': end}

    def test_compaction_is_counted_and_legacy_snapshots_are_not(self):
        self.write([{'type': 'turn_context', 'payload': {'model': 'model-a'}},
                    response('first', 10), {'type': 'compacted'}, response('compact', 20),
                    {'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {
                        'total_token_usage': {'total_tokens': 999999}}}}, response('compact', 20)])
        session, records, errors = read_codex(self.path)
        self.assertEqual((session, len(records), errors), ('s', 2, 0))
        self.assertEqual(sum(r['tokens'] for r in records), 214)
        self.assertEqual(records[0]['counts'], {'input_uncached': 20, 'cache_read_input': 80,
                                             'cache_write_input': 0, 'output': 7})

    def test_half_open_boundaries_and_model_switch_and_replay(self):
        self.write([{'type': 'turn_context', 'payload': {'model': 'model-a'}},
                    response('a', 9), {'type': 'turn_context', 'payload': {'model': 'model-b'}},
                    response('b', 10), response('c', 25)])
        bindings = [self.binding('project-a', 0, 10), self.binding('project-b', 10, 20)]
        method = self.method(bindings + bindings)
        result = execute(method)['result']
        groups = {g['bead']: g for g in result['groups']}
        self.assertEqual(groups['project-a']['model'], 'model-a')
        self.assertEqual(groups['project-b']['model'], 'model-b')
        self.assertEqual(groups['unattributed']['tokens'], 107)
        self.assertEqual(result, execute(method)['result'])

    def test_declared_focus_outranks_inferred_held_item(self):
        # aegis-59jx step 2a: inferred-held is used only where no declared focus covers.
        self.write([{'type': 'turn_context', 'payload': {'model': 'model-a'}},
                    response('a', 5), response('b', 15), response('c', 25)])
        held = {**self.binding('held', 0, 30), 'basis': 'inferred-held'}
        result = execute(self.method([self.binding('declared', 10, 20), held]))['result']
        by = {r['id']: (r['bead'], r['attribution']) for r in result['records']}
        self.assertEqual(by, {'a': ('held', 'inferred held item'), 'b': ('declared', 'attributed'),
                              'c': ('held', 'inferred held item')})
        groups = {g['bead']: g for g in result['groups']}
        self.assertEqual((groups['held']['basis'], groups['held']['coverage']), ('inferred-held', 'inferred'))
        self.assertNotIn('basis', groups['declared'])
        self.assertEqual(groups['declared']['coverage'], 'observed')
        self.assertEqual(result['coverage'], 'UNKNOWN')
        self.assertEqual(result['binding_bases'], ['declared', 'inferred-held'])

    def test_declared_groups_are_unchanged_by_the_basis_field(self):
        self.write([{'type': 'turn_context', 'payload': {'model': 'model-a'}}, response('a', 5)])
        plain = execute(self.method([self.binding('x', 0, 10)]))['result']
        explicit = execute(self.method([{**self.binding('x', 0, 10), 'basis': 'declared'}]))['result']
        self.assertEqual(plain['groups'], explicit['groups'])
        self.assertEqual(sorted(plain['groups'][0]), ['agent', 'bead', 'counts', 'coverage', 'harness',
                                                     'model', 'requests', 'rig', 'sessions', 'tokens'])

    def test_two_inferred_items_overlapping_are_unattributed(self):
        self.write([response('a', 5)])
        one, two = ({**self.binding(b, 0, 10), 'basis': 'inferred-held'} for b in ('p', 'q'))
        result = execute(self.method([one, two]))['result']
        self.assertEqual(result['records'][0]['attribution'], 'overlapping focus')

    def test_unknown_basis_is_refused(self):
        self.write([response('a', 5)])
        out = execute(self.method([{**self.binding('x', 0, 10), 'basis': 'guessed'}]))
        self.assertEqual(out['status'], 'invalid')
        self.assertIn('binding basis', out['error'])

    def test_overlapping_different_tasks_are_unattributed(self):
        self.write([response('a', 9)])
        result = execute(self.method([self.binding('a', 0, 20), self.binding('b', 1, 30)]))['result']
        self.assertEqual(result['groups'][0]['bead'], 'unattributed')
        self.assertEqual(result['records'][0]['attribution'], 'overlapping focus')

    def test_conflicting_identity_is_not_first_wins(self):
        self.write([response('a', 10), response('a', 10, output=99), response('a', 10)])
        self.assertEqual(read_codex(self.path)[1:], ([], 1))

    def test_unknown_cache_write_does_not_become_zero(self):
        row = response('a', 10)
        del row['payload']['usage']['cache_write_input_tokens']
        self.write([row])
        record = read_codex(self.path)[1][0]
        self.assertIsNone(record['counts']['cache_write_input'])
        self.assertIsNone(record['counts']['input_uncached'])
        self.assertEqual(record['tokens'], 107)

    def test_missing_source_and_legacy_only_are_unknown(self):
        result = execute(self.method([]))['result']
        self.assertEqual(result['coverage'], 'UNKNOWN')
        self.write([{'type': 'session_meta', 'payload': {'id': 's'}},
                    {'type': 'event_msg', 'payload': {'type': 'token_count'}}])
        self.assertEqual(execute(self.method([]))['result']['coverage'], 'UNKNOWN')

    def test_negative_boolean_and_conflicting_claude_usage_abstain(self):
        base = {'sessionId': 's', 'requestId': 'a', 'timestamp': '2026-09-14T12:00:00Z',
                'message': {'usage': {'input_tokens': 1, 'output_tokens': 2,
                           'cache_creation_input_tokens': 3, 'cache_read_input_tokens': 4}}}
        other = json.loads(json.dumps(base)); other['message']['usage']['output_tokens'] = 8
        self.write([base, other, base])
        self.assertEqual(read_claude(self.path)[1:], ([], 1))
        self.write([response('a', 10, inp=True), response('b', 20, output=-1)])
        self.assertEqual(read_codex(self.path)[1:], ([], 2))

    def test_truncated_flush_and_later_completion(self):
        self.write([response('a', 10)])
        with self.path.open('a') as f:
            f.write('{"type":')
        self.assertEqual(read_codex(self.path)[2], 1)
        self.write([response('a', 10), response('b', 20)])
        self.assertEqual(len(read_codex(self.path)[1]), 2)
        self.assertEqual(read_codex(self.path)[2], 0)

    def test_invalid_request_timestamp_reports_unknown(self):
        for stamp in (True, {"bad": "timestamp"}, "2026-09-14T12:00:00"):
            with self.subTest(stamp=stamp):
                self.write([response('a', stamp)])
                result = execute(self.method([]))['result']
                self.assertEqual(result['coverage'], 'UNKNOWN')
                self.assertIn('invalid request timestamp', result['errors'])
                self.assertEqual(result['groups'], [])

    def test_explicit_other_harness_binding_cannot_charge_request(self):
        self.write([response('a', 10)])
        binding = {**self.binding('project-a', 0, 20), 'harness': 'claude'}
        result = execute(self.method([binding]))['result']
        self.assertEqual(result['groups'][0]['bead'], 'unattributed')

    def test_later_session_metadata_cannot_relabel_prior_requests(self):
        self.write([response('a', 10), {'type': 'session_meta', 'payload': {'id': 'other'}}])
        with self.assertRaisesRegex(ValueError, 'mixed session identities'):
            read_codex(self.path)

    def test_replicated_sources_charge_once_and_disagreement_is_unknown(self):
        self.write([response('a', 10)])
        copy = self.path.with_name('copy.jsonl')
        copy.write_text(self.path.read_text())
        method = self.method([self.binding('project-a', 0, 20)])
        method['params']['sources'].append({'path': str(copy), 'harness': 'codex', 'agent': 'worker'})
        result = execute(method)['result']
        self.assertEqual(result['groups'][0]['tokens'], 107)
        copy.write_text(json.dumps(response('a', 10, output=8)) + '\n')
        result = execute(method)['result']
        self.assertEqual(result['coverage'], 'UNKNOWN')
        self.assertEqual(result['groups'], [])
