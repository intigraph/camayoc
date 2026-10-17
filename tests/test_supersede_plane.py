"""Exact governed replacement, never carrier-episode retraction."""
import unittest
from test_promote_plane import promote_plane as pp, ok, T1

OLD = (T1[0], T1[1], '"old"')


class SupersedeGateTests(unittest.TestCase):
    def args(self, **changes):
        args = ok(superseded=OLD, source_has=lambda t: True, target_has=lambda t: True)
        args.update(changes)
        return args

    def test_exact_replacement_is_one_conditional_named_graph_update(self):
        write = pp.supersede(**self.args())
        self.assertIn('DELETE { GRAPH', write['update'])
        self.assertIn('INSERT { GRAPH', write['update'])
        self.assertIn('WHERE {', write['update'])
        self.assertIn('FILTER NOT EXISTS', write['update'])
        self.assertIn('supersededTriple', write['update'])
        self.assertIn('replacementTriple', write['update'])
        self.assertNotIn('/episode/retract', str(write))

    def test_missing_old_or_new_is_refused_before_write(self):
        for changes in ({'target_has': lambda t: False}, {'source_has': lambda t: False}):
            with self.subTest(changes=changes), self.assertRaises(pp.PromotionRefused):
                pp.supersede(**self.args(**changes))

    def test_single_same_property_and_changed_object_required(self):
        for triples in ([T1, T1], [OLD], [(T1[0], '<urn:other>', T1[2])], []):
            with self.subTest(triples=triples), self.assertRaises(pp.PromotionRefused):
                pp.supersede(**self.args(triples=triples))

    def test_existing_authority_and_no_self_promotion_are_preserved(self):
        for changes in ({'grants': {}}, {'promoted_by': 'claude'}, {'target_plane': 'crew:inferred'}):
            with self.subTest(changes=changes), self.assertRaises(pp.PromotionRefused):
                pp.supersede(**self.args(**changes))

    def test_form_transport_preserves_terms_and_existing_auth_provenance(self):
        import urllib.parse
        from unittest import mock
        body = {'update': 'INSERT DATA { <urn:s> <urn:p> "a+b&c=é" }',
                'actor': 'alice', 'source': 'exact replacement'}
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{}'
        with mock.patch.dict('os.environ', {}, clear=True), \
                mock.patch.object(pp.planes, 'AUTH', 'isolated-test-fixture'), \
                mock.patch.object(pp.planes.quipu_auth, '_disabled', set()), \
                mock.patch('urllib.request.urlopen', return_value=response) as send:
            pp.planes._post('/update', body, client='camayoc-planes', form=True)
        request = send.call_args.args[0]
        self.assertEqual({k: [v] for k, v in body.items()},
                         urllib.parse.parse_qs(request.data.decode()))
        headers = {k.lower(): v for k, v in request.header_items()}
        self.assertEqual('Bearer isolated-test-fixture', headers['authorization'])
        self.assertEqual('camayoc-planes', headers['x-quipu-client'])
        self.assertEqual('application/x-www-form-urlencoded', headers['content-type'])

    def test_zero_or_wrong_graph_report_is_never_success_or_retried(self):
        from unittest import mock
        write = pp.supersede(**self.args())
        for report in (None, [], {'tx': None, 'retracted': 0, 'graphs': []},
                       {'tx': 1, 'asserted': 1, 'retracted': 1, 'graphs': [{'graph': None}]},
                       {'tx': 1, 'asserted': 1, 'retracted': 1, 'graphs': [None]}):
            with self.subTest(report=report), \
                    mock.patch.object(pp.planes, '_post', return_value=report) as send, \
                    mock.patch.object(pp, 'ask_in_graph') as read:
                self.assertEqual(3, pp.commit_supersede(write, OLD, T1))
                self.assertEqual(1, send.call_count)
                read.assert_not_called()

    def test_old_fact_changes_record_identity(self):
        a = pp.supersede(**self.args())
        b = pp.supersede(**self.args(superseded=(T1[0], T1[1], '"different"')))
        self.assertNotEqual(a['digest'], b['digest'])


import test_promote_plane as promotion_tests


class SupersedeStoreTests(promotion_tests.PromotionAgainstARealStoreTests):
    # Reuse ONLY the existing private-server fixture, not its ordered tests.
    test_1_the_mutant_record_only_promotion_moves_nothing = None
    test_2_a_real_promotion_puts_the_triple_in_the_target_plane = None

    def fixture(self, suffix):
        subject, predicate = f'<urn:supersede:{suffix}>', '<urn:claim>'
        old, new = (subject, predicate, '"old"'), (subject, predicate, '"new"')
        siblings = [(subject, f'<urn:other:{i}>', f'"kept{i}"') for i in range(9)]
        graph = pp.planes.PLANES['crew:declared']['iri']
        source = pp.planes.PLANES['crew:inferred']['iri']
        nt = lambda ts: '\n'.join(' '.join(t) + ' .' for t in ts)
        self.call('/knot', {'graph': graph, 'turtle': nt([old] + siblings), 'actor': 'fixture'})
        self.call('/knot', {'graph': source, 'turtle': nt([new]), 'actor': 'fixture'})
        return old, new, siblings, graph, source

    def present(self, graph, triple, **extra):
        return self.call('/query', {'query': f'ASK {{ GRAPH <{graph}> {{ {" ".join(triple)} . }} }}', **extra})['result']

    def test_exact_ten_fact_replacement_preserves_history_and_siblings(self):
        import datetime
        import json
        import os
        import subprocess
        import sys
        import time
        from pathlib import Path
        old, new, siblings, graph, source = self.fixture('happy')
        root_probe = 'ASK { ' + ' '.join(old) + ' . }'
        self.call('/knot', {'turtle': ' '.join(old) + ' .', 'actor': 'root-control'})
        self.assertTrue(self.call('/query', {'query': root_probe})['result'])
        self.assertTrue(all(self.present(graph, t) for t in [old] + siblings))
        cut = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        self.assertTrue(self.present(graph, old, valid_at=cut))
        # Store write timestamps have one-second precision. Cross that boundary
        # before closing so the as-of cut is strictly inside the old interval.
        time.sleep(1.05)
        old_file, new_file = Path(self.temp.name) / 'old.nt', Path(self.temp.name) / 'new.nt'
        old_file.write_text(' '.join(old) + ' .\n')
        new_file.write_text(' '.join(new) + ' .\n')
        env = dict(os.environ, QUIPU_SERVER=self.base, CAMAYOC_AUTHORITY=str(self.auth),
                   QUIPU_AUTH_TOKEN='isolated-test-fixture')
        result = subprocess.run([sys.executable, str(pp.ROOT / 'promote_plane.py'),
            '--triples-file', str(new_file), '--supersedes', str(old_file), '--to', 'crew:declared',
            '--by', 'bob', '--authored-by', 'claude', '--reason', 'private exact replacement'],
            env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(self.present(graph, new))
        self.assertFalse(self.present(graph, old))
        self.assertTrue(self.present(graph, old, valid_at=cut))
        self.assertTrue(all(self.present(graph, t) for t in siblings))
        self.assertTrue(self.present(source, new))
        self.assertTrue(self.call('/query', {'query': root_probe})['result'])
        rows = self.call('/query', {'query': f'SELECT ?o WHERE {{ GRAPH <{graph}> {{ {new[0]} {new[1]} ?o }} }}'})
        self.assertEqual(1, len(rows['rows']))
        audit = ('PREFIX c: <https://camayoc.local/ontology/> '
                 f'ASK {{ GRAPH <{graph}> {{ ?r c:supersededTriple {json.dumps(" ".join(old) + " .")} ; '
                 f'c:replacementTriple {json.dumps(" ".join(new) + " .")} . }} }}')
        self.assertTrue(self.call('/query', {'query': audit})['result'])

    def test_concurrent_target_change_refuses_whole_update(self):
        old, new, siblings, graph, source = self.fixture('race')
        write = pp.supersede([new], 'crew:declared', 'bob', 'claude', 'private race',
                             '2026-01-01T00:00:00Z', old,
                             grants={'bob': ['crew:declared']}, source_has=lambda t: True,
                             target_has=lambda t: True)
        # A separate legitimate writer changes the target after preflight.
        third = (old[0], old[1], '"concurrent"')
        self.call('/knot', {'graph': graph, 'turtle': ' '.join(third) + ' .', 'actor': 'other'})
        import urllib.parse
        import urllib.request
        import json
        body = urllib.parse.urlencode({k: write[k] for k in ('update', 'actor', 'source')}).encode()
        req = urllib.request.Request(self.base + '/update', data=body,
                                     headers={'Content-Type': 'application/x-www-form-urlencoded'})
        with urllib.request.urlopen(req, timeout=10) as response:
            result = json.loads(response.read())
        self.assertIsNone(result['tx'])
        self.assertEqual((0, 0), (result['asserted'], result['retracted']))
        self.assertTrue(self.present(graph, old))
        self.assertTrue(self.present(graph, third))
        self.assertFalse(self.present(graph, new))
        self.assertTrue(all(self.present(graph, t) for t in siblings))
