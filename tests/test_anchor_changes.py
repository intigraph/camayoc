"""Acceptance controls for the pending file/entity anchor profile."""
import sys
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from anchor_changes import Binding, Unknown, judge, route

A, B = 'a' * 64, 'b' * 64


def binding(**kw):
    fields = dict(id='urn:binding:1', source_graph='urn:graph:code',
                  source='urn:file:one', target_graph='urn:graph:knowledge',
                  target='urn:entity:one', expected_hash=A, recipe='file-sha256-v1',
                  owner='fixture-owner', direction='file-to-entity', epoch='fixture-epoch', version=4)
    return Binding(**{**fields, **kw})


def version(b, value=B, tx=7, **kw):
    return dict(graph=b.source_graph, entity=b.source, hash=value,
                version=tx, recipe=b.recipe, epoch=b.epoch, **kw)


class Anchors(unittest.TestCase):
    def test_file_change_is_due_with_exact_pair_and_evidence(self):
        b = binding(); r = judge(b, version(b))
        self.assertEqual(r['verdict'], 'DUE')
        self.assertEqual(r['target'], b.target)
        self.assertEqual((r['basis']['old_hash'], r['basis']['new_hash']), (A, B))
        self.assertEqual(r['owner'], 'fixture-owner')

    def test_same_hash_and_missing_row_are_distinct(self):
        b = binding()
        self.assertEqual(judge(b, version(b, A))['verdict'], 'NOT_DUE')
        self.assertEqual(judge(b, None)['verdict'], 'UNKNOWN')

    def test_entity_to_file_has_its_own_baseline(self):
        b = binding(direction='entity-to-file', source='urn:entity:one',
                    target='urn:file:docs', recipe='canonical-rdf-sha256-v1')
        self.assertEqual(judge(b, version(b))['direction'], 'entity-to-file')
        self.assertEqual(judge(b, version(b))['target'], 'urn:file:docs')

    def test_multiple_files_do_not_share_one_entity_hash(self):
        first = binding(); second = binding(id='urn:binding:2', source='urn:file:two', expected_hash=B)
        self.assertEqual(judge(first, version(first))['verdict'], 'DUE')
        self.assertEqual(judge(second, version(second))['verdict'], 'NOT_DUE')

    def test_replay_stable_but_later_same_hash_cycle_is_new(self):
        b = binding()
        self.assertEqual(judge(b, version(b)), judge(b, version(b)))
        self.assertNotEqual(judge(b, version(b, tx=7))['event_id'],
                            judge(b, version(b, tx=9))['event_id'])

    def test_graph_and_binding_identity_separate_event_ids(self):
        b = binding(); other = replace(b, id='urn:binding:2', source_graph='urn:graph:other')
        self.assertNotEqual(judge(b, version(b))['event_id'], judge(other, version(other))['event_id'])
        with self.assertRaises(Unknown): judge(b, version(other))

    def test_only_explicit_deletion_is_due(self):
        b = binding(); r = judge(b, version(b, None, deleted=True))
        self.assertEqual(r['verdict'], 'DUE'); self.assertTrue(r['basis']['deleted'])
        self.assertIsNone(r['basis']['new_hash'])

    def test_recipe_mismatch_is_unknown_not_false_staleness(self):
        b = binding(); v = version(b); v['recipe'] = 'different'
        self.assertEqual(judge(b, v)['verdict'], 'UNKNOWN')

    def test_bad_versions_hashes_and_iris_refuse(self):
        b = binding()
        for value in (True, -1, None, '7'):
            with self.subTest(value=value), self.assertRaises(Unknown): judge(b, version(b, tx=value))
        for value in ('bad', 'B' * 64, None):
            with self.subTest(value=value), self.assertRaises(Unknown): judge(b, version(b, value))
        with self.assertRaises(Unknown): binding(source='file relative')

    def test_idle_no_reads_and_changed_subject_only(self):
        calls = []
        def lookup(g, e): calls.append((g, e)); return ['urn:binding:1']
        self.assertEqual(route([], lookup), []); self.assertFalse(calls)
        result = route([{'graph':'urn:g', 'entity':'urn:e'}] * 2, lookup)
        self.assertEqual(calls, [('urn:g', 'urn:e')]); self.assertEqual(result, ['urn:binding:1'])

    def test_overflow_and_incomplete_lookup_refuse_whole_batch(self):
        with self.assertRaises(Unknown): route([{}] * 65, lambda *a: [])
        with self.assertRaises(Unknown): route([dict(graph='urn:g', entity='urn:e')], lambda *a: ['urn:b'] * 65)
        def unavailable(*a): raise Unknown('truncated')
        with self.assertRaises(Unknown): route([dict(graph='urn:g', entity='urn:e')], unavailable)

    def test_new_epoch_or_old_snapshot_is_unknown(self):
        b = binding(); v = version(b); v['epoch'] = 'different-epoch'
        self.assertEqual(judge(b, v)['verdict'], 'UNKNOWN')
        self.assertEqual(judge(b, version(b, tx=3))['verdict'], 'UNKNOWN')

    def test_owner_absence_stays_absent(self):
        b = binding(owner=None); self.assertIsNone(judge(b, version(b))['owner'])


if __name__ == '__main__': unittest.main()
