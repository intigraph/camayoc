"""Catalog admission, native graph projection and bounded README publication."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('delivery_catalog', ROOT / 'scripts/render-delivery-catalog.py')
catalog = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(catalog)
SOURCE = ROOT / 'catalog/delivery-emitters.json'
BINARY = os.environ.get('QUIPU_BIN') or shutil.which('quipu')


class CatalogAdmissionTests(unittest.TestCase):
    def test_incomplete_duplicate_and_unsafe_records_refuse(self):
        original = json.loads(SOURCE.read_text())
        for kind in ['missing', 'duplicate', 'url', 'wrong-type', 'duplicate-order', 'missing-doc', 'missing-evidence', 'observed-status']:
            value = copy.deepcopy(original)
            if kind == 'missing':
                del value['nodes'][0]['properties']['emitterStatus']
            elif kind == 'duplicate':
                value['nodes'].append(copy.deepcopy(value['nodes'][0]))
            elif kind == 'url':
                value['nodes'][0]['properties']['emitterDocs'] += ') [x](https://example.invalid)'
            elif kind == 'missing-evidence':
                del value['nodes'][0]['properties']['emitterEvidence']
            elif kind == 'observed-status':
                value['nodes'][0]['properties']['sourceKind'] = 'observed'
            elif kind == 'missing-doc':
                value['nodes'][0]['properties']['emitterDocs'] = 'https://github.com/intigraph/camayoc/blob/main/not-a-real-guide.md'
            elif kind == 'wrong-type':
                value['nodes'][0]['type'] = 'UnregisteredEmitter'
            else:
                value['nodes'][0]['properties']['emitterOrder'] = value['nodes'][1]['properties']['emitterOrder']
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'catalog.json'
                path.write_text(json.dumps(value))
                with self.subTest(kind=kind), self.assertRaises(ValueError):
                    catalog.load_catalog(path)

    def test_duplicate_json_fields_are_not_last_value_wins(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'catalog.json'
            path.write_text('{"nodes":[],"nodes":[]}')
            with self.assertRaisesRegex(ValueError, 'duplicate'):
                catalog.load_catalog(path)

    def test_readme_update_preserves_every_byte_outside_the_owned_block(self):
        old = 'intro\n'+catalog.START+'\nold\n'+catalog.END+'\n## The stack\nkeep me\n'
        new = catalog.START+'\nnew\n'+catalog.END+'\n'
        self.assertEqual('intro\n'+new+'## The stack\nkeep me\n', catalog.project(old, new))
        crlf = 'intro\r\n## The stack\r\nkeep me\r\n'
        self.assertEqual('intro\r\n'+new+'\n## The stack\r\nkeep me\r\n', catalog.project(crlf, new))
        for bad in [old+catalog.START, old.replace(catalog.END, ''), 'no known insertion point']:
            with self.assertRaises(ValueError):
                catalog.project(bad, new)

    def test_cells_are_text_and_cannot_add_columns_html_or_links(self):
        value = catalog.cell('<b>x</b>|[link](javascript:x)\nnext')
        self.assertNotIn('<b>', value)
        self.assertIn(r'\|', value)
        self.assertIn(r'\[link\]\(javascript:x\)', value)
        self.assertNotIn('\n', value)

    def test_incomplete_native_result_is_not_an_empty_catalog(self):
        payload = json.loads(SOURCE.read_text())
        for answer in [{'rows': [], 'count': 0, 'truncated': False},
                       {'rows': [{}]*len(payload['nodes']), 'count': len(payload['nodes']), 'truncated': True}]:
            with patch.object(catalog, 'native_call', return_value=[{}, answer]):
                with self.assertRaises(ValueError):
                    catalog.graph_rows(payload, '/unused')


@unittest.skipUnless(BINARY, 'native Quipu CLI not installed; required CLI CI arm supplies it')
class NativeCatalogTests(unittest.TestCase):
    def test_current_readme_is_the_actual_graph_projection(self):
        block = catalog.render_catalog(SOURCE, BINARY)
        text = (ROOT / 'README.md').read_text()
        self.assertEqual(text, catalog.project(text, block))
        self.assertEqual(1, block.count('| Ansible |'))
        self.assertIn('not installed producers', block)

    def test_adding_data_adds_a_row_without_renderer_code_or_host_state(self):
        payload = json.loads(SOURCE.read_text())
        extra = copy.deepcopy(payload['nodes'][0])
        extra['name'] = 'camayoc-emitter-example'
        extra['properties'].update(emitterOrder='6', emitterTool='Example adapter')
        payload['nodes'].append(extra)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'catalog.json'
            path.write_text(json.dumps(payload))
            self.assertIn('| Example adapter |', catalog.render_catalog(path, BINARY))

    def test_drift_check_refuses_and_does_not_rewrite_the_readme(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'README.md'
            original = (ROOT / 'README.md').read_text().replace('| Ansible |', '| altered |')
            path.write_text(original)
            p = subprocess.run([sys.executable, str(ROOT / 'scripts/render-delivery-catalog.py'),
                '--quipu', BINARY, '--readme', str(path), '--check'],
                capture_output=True, text=True, timeout=30)
            self.assertEqual(1, p.returncode)
            self.assertIn('differs from the queried graph', p.stderr)
            self.assertEqual(original, path.read_text())


if __name__ == '__main__':
    unittest.main()
