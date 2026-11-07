"""Offline source-equivalence and raw-alias demonstration; no store access."""
import json
from pathlib import Path
import sys

from rdflib import Graph, RDF, URIRef
from rdflib.compare import isomorphic

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT/'tests'))
from test_aegis_work_aliases import answers, populated, AEGIS, SKOS  # noqa: E402

manifest = json.loads((ROOT/'shapes/aegis/inference-placement.json').read_text())
before, after = Graph(), Graph()
paths = set()
for source in manifest['sources']:
    before += Graph().parse(ROOT/source['path'])
    paths.update(row['path'] for row in source['assignments'])
for path in sorted(paths):
    graph = Graph().parse(ROOT/path)
    print(f'{Path(path).name}: {len(graph)} triples')
    after += graph
assert isomorphic(before, after)
print(f'original {len(before)} triples -> split union {len(after)} triples; RDF-isomorphic')
result = answers(populated())
assert all(items == result['canonical'] for items in result.values())
print('raw fixture: canonical=WorkItem=Bead=Vtodo; 2 Action IDs, distinct calendar IDs')
item = URIRef('https://example.org/work/one')
graph = populated()
graph.remove((item, RDF.type, AEGIS.Bead))
result = answers(graph)
assert result['Bead'] != result['canonical']
print('negative: remove explicit Bead type -> Bead=1, canonical=2; equality REFUSED')
graph = populated()
graph.remove((item, SKOS.exactMatch, None))
result = answers(graph)
assert result['Vtodo'] != result['canonical']
print('negative: remove calendar link -> Vtodo=1, canonical=2; equality REFUSED')
print('source-only: no network, load, rewrite, inference activation or removal')
