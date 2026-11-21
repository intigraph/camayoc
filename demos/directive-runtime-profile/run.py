"""Compare isolated before/after release constraints; no live writes."""
import runpy
import subprocess
from pathlib import Path
from rdflib import Graph, Namespace
from pyshacl import validate

ROOT = Path(__file__).resolve().parents[3]
BASE = '663d307b7da61c74431817a348246dffb74df333'
controls = runpy.run_path(str(ROOT / 'tests/test_aegis_directive_deadlines.py'))['DirectiveDeadlines']
controls.setUpClass()
case = controls()
old = Graph().parse(data=subprocess.check_output([
    'git', '-C', str(ROOT), 'show', BASE + ':tests/fixtures/aegis-upstream/legacy-compatibility.shapes.ttl'
]), format='turtle')
print('Isolated source fixtures; no loaded-set claim')
print('Base:', BASE)
print('Candidate: software-factory compatibility SHA256 fd82a0e0533a772ada0f9945b4b986c5b1a79781030b662a2fe7606b0d391cc3')
sh = Namespace('http://www.w3.org/ns/shacl#')
for name, deadline in [('typed zoned deadline', ';q:traceabilityDueAt "2030-12-05T00:00:00Z"^^xsd:dateTime'), ('missing deadline', '')]:
    data = case.fixture(deadline)
    print('Input: three Directives,', name)
    for label, shapes, expected in [('before', old, True), ('candidate', controls.shapes, bool(deadline))]:
        conforms, result, report = validate(data, shacl_graph=shapes, advanced=True, allow_warnings=True)
        severity = sorted({str(x).rsplit('#', 1)[-1] for x in result.objects(None, sh.resultSeverity)})
        print(f'  {label}: admitted={conforms}, severities={severity}')
        assert conforms == expected, report
print('PASS: dated admission retained; undated untraced directives now refused')
