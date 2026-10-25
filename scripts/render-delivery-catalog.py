#!/usr/bin/env python3
"""Render the public delivery table from catalog Tool records through Quipu.

Uses a disposable local store, never a server, existing database or credential.
Without --write or --check the generated Markdown goes to stdout. The same
block can be projected into another repository's README with --readme.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tempfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
START = '<!-- delivery-catalog:start -->'
END = '<!-- delivery-catalog:end -->'
NS = 'http://aegis.gastown.local/ontology/'
COLUMNS = ['emitterTool', 'emitterStandard', 'emitterHow', 'emitterStatus', 'emitterDocs']


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate catalog field: ' + key)
        result[key] = value
    return result


def load_catalog(path):
    raw = Path(path).read_bytes()
    catalog = json.loads(raw, object_pairs_hook=unique)
    nodes = catalog.get('nodes')
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 50:
        raise ValueError('catalog needs 1..50 Tool records')
    seen, order = set(), set()
    for node in nodes:
        name, props = node.get('name'), node.get('properties', {})
        if (not isinstance(name, str) or not re.fullmatch(r'camayoc-emitter-[a-z0-9-]+', name)
                or name in seen or node.get('type') != 'Tool'):
            raise ValueError('invalid or duplicate Tool identity')
        seen.add(name)
        if props.get('sourceKind') != 'declared':
            raise ValueError('catalog support is a declared claim, not runtime observation')
        if props.get('emitterCatalog') != 'camayoc-delivery-v1':
            raise ValueError('unexpected catalog family')
        number = props.get('emitterOrder')
        if not isinstance(number, str) or not re.fullmatch(r'[1-9][0-9]?', number) or number in order:
            raise ValueError('invalid or duplicate catalog order')
        order.add(number)
        for field in COLUMNS:
            value = props.get(field)
            if not isinstance(value, str) or not value.strip() or len(value) > 1000:
                raise ValueError('missing or invalid ' + field)
        for field in ['emitterDocs', 'emitterEvidence']:
            url = urlsplit(props.get(field, ''))
            if (url.scheme != 'https' or url.netloc != 'github.com' or url.username
                    or not re.fullmatch(r'/intigraph/camayoc/blob/main/[A-Za-z0-9_./-]+', url.path)
                    or url.query or url.fragment):
                raise ValueError('documentation and evidence must name public Camayoc source')
            relative = PurePosixPath(url.path.removeprefix('/intigraph/camayoc/blob/main/'))
            if '..' in relative.parts or not (ROOT / relative).is_file():
                raise ValueError('documentation or evidence source file does not exist')
    return raw, catalog


def native_call(binary, database, calls):
    spec = importlib.util.spec_from_file_location('catalog_local_mcp', ROOT / 'scripts/build-repository-share.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.local_mcp(binary, database, calls)


def graph_rows(catalog, binary):
    names = {n['name'] for n in catalog['nodes']}
    values = ' '.join('<' + NS + name + '>' for name in sorted(names))
    query = ('PREFIX a: <' + NS + '> SELECT ?entry ?tool ?standard ?how ?status ?docs ?order WHERE { '
             'VALUES ?entry { ' + values + ' } ?entry a a:Tool ; '
             'a:emitterCatalog "camayoc-delivery-v1" ; a:emitterTool ?tool ; '
             'a:emitterStandard ?standard ; a:emitterHow ?how ; '
             'a:emitterStatus ?status ; a:emitterDocs ?docs ; a:emitterOrder ?order . } '
             'LIMIT ' + str(len(names) + 1))
    with tempfile.TemporaryDirectory(prefix='camayoc-catalog-') as folder:
        store = Path(folder) / 'catalog.db'
        _loaded, answer = native_call(Path(binary), store,
            [('quipu_episode', catalog), ('quipu_query', {'query': query})])
    rows = answer.get('rows')
    if (not isinstance(rows, list) or answer.get('count') != len(names)
            or len(rows) != len(names) or answer.get('truncated') is not False):
        raise ValueError('incomplete or overflowing catalog query')
    expected = {NS + name for name in names}
    if {r.get('entry') for r in rows} != expected:
        raise ValueError('catalog graph identities changed')
    for row in rows:
        if not all(isinstance(row.get(k), str) and row[k] for k in ['tool', 'standard', 'how', 'status', 'docs', 'order']):
            raise ValueError('invalid graph row')
    return sorted(rows, key=lambda r: int(r['order']))


def cell(value):
    value = html.escape(value, quote=False).replace('\r', ' ').replace('\n', ' ')
    return re.sub(r'([\\`*_[\]()|])', r'\\\1', value)


def render_catalog(path, binary):
    raw, catalog = load_catalog(path)
    rows = graph_rows(catalog, binary)
    lines = [START, '', '## Delivery events', '',
        'Bring deployment outcomes and change history into the same governed memory.',
        'These adapters use CDEvents on CloudEvents and Camayoc\'s single ingress.', '',
        '[Catalog data](https://github.com/intigraph/camayoc/blob/main/catalog/delivery-emitters.json) · '
        '[Updating the table](https://github.com/intigraph/camayoc/blob/main/catalog/README.md)', '',
        '| Tool | Standard | How | Status | Docs |', '| --- | --- | --- | --- | --- |']
    for r in rows:
        lines.append('| ' + ' | '.join([cell(r[k]) for k in ['tool', 'standard', 'how', 'status']]
                                      + ['[Guide](' + r['docs'] + ')']) + ' |')
    lines.extend(['', 'The catalog describes source support, not installed producers or measured event coverage.',
        'The graph queries for deployment frequency, change failure rate, lead time and restore time',
        'need recorded events for the selected service, repository and time window.',
        'Missing change or deployment history is not a measured zero.', '',
        '<!-- catalog-source-sha256: ' + hashlib.sha256(raw).hexdigest() + ' -->', END])
    return '\n'.join(lines) + '\n'


def project(text, block):
    if START in text or END in text:
        if text.count(START) != 1 or text.count(END) != 1 or text.index(START) >= text.index(END):
            raise ValueError('ambiguous catalog markers')
        a, b = text.index(START), text.index(END) + len(END)
        return text[:a] + block.rstrip('\n') + text[b:]
    anchors = list(re.finditer(r'(?m)^## The stack\r?$', text))
    if len(anchors) != 1:
        raise ValueError('README needs one stack heading or an existing catalog block')
    offset = anchors[0].start()
    return text[:offset] + block + '\n' + text[offset:]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--catalog', type=Path, default=ROOT / 'catalog/delivery-emitters.json')
    ap.add_argument('--quipu', default=os.environ.get('QUIPU_BIN') or shutil.which('quipu'))
    ap.add_argument('--readme', type=Path, default=ROOT / 'README.md')
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument('--write', action='store_true')
    mode.add_argument('--check', action='store_true')
    args = ap.parse_args(argv)
    if not args.quipu:
        ap.error('Quipu CLI required to query the catalog graph')
    try:
        block = render_catalog(args.catalog, args.quipu)
        if not args.write and not args.check:
            print(block, end='')
            return 0
        original = args.readme.read_bytes().decode('utf-8')
        result = project(original, block)
        if args.check:
            if original != result:
                raise ValueError('README catalog differs from the queried graph')
            print('README catalog matches the queried graph')
        else:
            args.readme.write_bytes(result.encode('utf-8'))
        return 0
    except (OSError, ValueError, KeyError) as exc:
        print('catalog: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
