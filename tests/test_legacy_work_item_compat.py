"""The legacy WorkItem compatibility set restores what core dropped (aegis-452).

Core no longer targets aegis:WorkItem, so without this set an invalid tracker
projection record validates clean. These tests run the real `quipu validate`
over core alone and core plus the compatibility set, on the same records.
"""
from pathlib import Path
import subprocess
import tempfile
import unittest

from rdflib import Graph, Namespace, RDF, RDFS, SH

from quipu_bin_guard import QUIPU, requires_quipu

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / 'shapes/core.shapes.ttl'
COMPAT = ROOT / 'shapes/legacy-work-item.shapes.ttl'
A = Namespace('http://aegis.gastown.local/ontology/')

PREFIXES = ('@prefix aegis: <http://aegis.gastown.local/ontology/> .\n'
            '@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n')
VALID = PREFIXES + ('aegis:w1 a aegis:WorkItem ; rdfs:label "work" ; '
                    'aegis:sourceKind "observed" ; aegis:outcome "done" .\n')
INVALID = {
    'sourceKind outside the closed set': PREFIXES + (
        'aegis:w2 a aegis:WorkItem ; rdfs:label "work" ; aegis:sourceKind "guessed" .\n'),
    'two labels': PREFIXES + (
        'aegis:w3 a aegis:WorkItem ; rdfs:label "a", "b" ; aegis:sourceKind "observed" .\n'),
    'a decaying outcome': PREFIXES + (
        'aegis:w4 a aegis:WorkItem ; rdfs:label "work" ; aegis:sourceKind "observed" ; '
        'aegis:outcome "in progress" .\n'),
}


def validate(shapes, data):
    """Return (exit code, output) of quipu validate; nonzero means not conforming."""
    with tempfile.TemporaryDirectory() as tmp:
        shapes_path, data_path = Path(tmp) / 'shapes.ttl', Path(tmp) / 'data.ttl'
        shapes_path.write_text(shapes)
        data_path.write_text(data)
        run = subprocess.run([str(QUIPU), 'validate', '--shapes', str(shapes_path),
                              '--data', str(data_path)], capture_output=True, text=True, timeout=60)
    return run.returncode, run.stdout + run.stderr


class CompatibilityScope(unittest.TestCase):
    def test_core_no_longer_targets_work_items_so_the_set_is_needed(self):
        core = Graph().parse(CORE)
        self.assertNotIn((None, SH.targetClass, A.WorkItem), core)

    def test_the_set_adds_only_what_core_dropped(self):
        compat, core = Graph().parse(COMPAT), Graph().parse(CORE)
        shapes = set(compat.subjects(RDF.type, SH.NodeShape))
        self.assertEqual(shapes, {A.CamayocWorkItemShape})
        paths = {compat.value(p, SH.path) for p in compat.objects(A.CamayocWorkItemShape, SH.property)}
        self.assertEqual(paths, {A.sourceKind, RDFS.label, A.outcome})
        # Core already governs these through targetSubjectsOf; repeating them here
        # would make the two sets disagree when either one changes.
        for covered in (A.inWorkflowRun, A.blockedOn):
            self.assertIn((None, SH.targetSubjectsOf, covered), core)
            self.assertNotIn(covered, paths)


@requires_quipu
class CompatibilityValidation(unittest.TestCase):
    def test_core_alone_accepts_each_invalid_record(self):
        # The defect this set exists for: without it, nothing refuses these.
        for name, data in INVALID.items():
            with self.subTest(record=name):
                code, out = validate(CORE.read_text(), data)
                self.assertEqual(code, 0, out)

    def test_core_with_the_set_refuses_each_invalid_record(self):
        shapes = CORE.read_text() + '\n' + COMPAT.read_text()
        for name, data in INVALID.items():
            with self.subTest(record=name):
                code, out = validate(shapes, data)
                self.assertNotEqual(code, 0, out)
                self.assertIn('camayoc:', out)

    def test_core_with_the_set_accepts_a_valid_record(self):
        code, out = validate(CORE.read_text() + '\n' + COMPAT.read_text(), VALID)
        self.assertEqual(code, 0, out)


if __name__ == '__main__':
    unittest.main()
