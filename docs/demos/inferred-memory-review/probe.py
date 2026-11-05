"""One isolated memory obligation and a non-memory negative control."""
import datetime as dt
import json
import sys
from pathlib import Path

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDF

sys.path.insert(0, str(Path(sys.argv[1]) / 'scripts'))
import change_adapter as ca
import planes
import review_due as rd

now = dt.datetime(2026, 11, 4, tzinfo=dt.timezone.utc)
inferred = Graph()
for name in ['memory', 'fact']:
    inferred.add((URIRef(rd.A + name), URIRef(rd.A + 'reviewAfter'), Literal('2026-10-01T00:00:00Z')))
inferred.add((URIRef(rd.A + 'memory'), RDF.type, URIRef(rd.A + 'Preference')))
inferred.add((URIRef(rd.A + 'memory'), URIRef(rd.A + 'naturalKey'), URIRef(rd.A + 'key')))


def post(endpoint, body):
    assert endpoint == '/query'
    graph = inferred if body.get('graph') == planes.plane_for('inferred') else Graph()
    rows = [{str(k): str(v) for k, v in row.asdict().items()} for row in graph.query(body['query'])]
    return {'rows': rows, 'truncated': False}


full = [(r['entity'], r['verdict']) for r in rd.evaluate(post, now=now)]
incremental = [(r['entity'], r['verdict']) for r in ca.evaluate('review', post, {
    'items': ['memory', 'fact'], 'now': now.timestamp()})['records']]
print(json.dumps({'full': full, 'incremental': incremental, 'nonmemory_excluded': all(e != 'fact' for e, _ in full + incremental)}, indent=2))
assert all(e != 'fact' for e, _ in full + incremental)
if '--require-fixed' in sys.argv:
    assert full == incremental == [('memory', 'DUE')]
else:
    assert full == incremental == []
