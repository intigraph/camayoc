"""Actual staged RDF, deterministic output, and refused partial migrations."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from rdflib import Graph, SH, URIRef
from rdflib.compare import isomorphic

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tests/fixtures/aegis-upstream/legacy-compatibility.shapes.ttl"
SPEC = importlib.util.spec_from_file_location("compatibility", ROOT / "scripts/build_aegis_compatibility.py")
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


class CompatibilityBundle(unittest.TestCase):
    def test_actual_bundle_preserves_original_targets_and_constraints(self):
        artifacts = BUILDER.compatibility_bundle(ROOT, SOURCE)
        manifest = json.loads(artifacts["manifest.json"])
        union = Graph()
        for row in manifest["files"]:
            union += Graph().parse(data=artifacts[row["path"]], format="turtle")
        original = Graph().parse(SOURCE)
        self.assertTrue(isomorphic(union, original))
        self.assertGreater(manifest["node_shapes"], 100)  # populated coverage control
        self.assertFalse(manifest["automatic_load"])
        self.assertEqual(sum(r["role"] == "engine-retained-candidate" for r in manifest["files"]), 1)
        engine = [r for r in manifest["files"] if r["role"] == "engine-retained-candidate"]
        self.assertEqual(engine[0]["node_shapes"], 1)
        self.assertEqual(sum(r["node_shapes"] for r in manifest["files"]
                             if r["role"] == "domain-compatibility-candidate"), 119)
        self.assertEqual(manifest["engine_claims"], {BUILDER.ENGINE_SHAPE: BUILDER.ENGINE_CLAIM})
        legacy = URIRef("http://aegis.gastown.local/ontology/WorkItem")
        self.assertGreater(len(list(union.subjects(SH.targetClass, legacy))), 0)
        # The public profile remains unchanged: compatibility is a separate artifact.
        profile = Graph().parse(ROOT / "shapes/aegis/public-term-profiles.shapes.ttl")
        self.assertEqual(len(list(profile.subjects(SH.targetClass, legacy))), 0)

    def test_separate_builds_have_identical_bytes(self):
        self.assertEqual(BUILDER.compatibility_bundle(ROOT, SOURCE),
                         BUILDER.compatibility_bundle(ROOT, SOURCE))

    def test_cli_creates_once_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "bundle"
            command = [sys.executable, str(ROOT / "scripts/build_aegis_compatibility.py"),
                       "--source", str(SOURCE), "--output", str(output)]
            first = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            expected = {p.name: p.read_bytes() for p in output.iterdir()}
            second = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(second.returncode, 2)
            self.assertEqual({p.name: p.read_bytes() for p in output.iterdir()}, expected)

    def test_wrong_source_refuses_before_output_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            wrong = Path(directory) / "wrong.ttl"
            wrong.write_bytes(SOURCE.read_bytes() + b"\n# changed source\n")
            output = Path(directory) / "bundle"
            result = subprocess.run([sys.executable, str(ROOT / "scripts/build_aegis_compatibility.py"),
                                     "--source", str(wrong), "--output", str(output)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn("source hash", result.stderr)
            self.assertFalse(output.exists())

    def test_missing_constraint_and_duplicate_assignment_refuse(self):
        for mutation in ("constraint", "duplicate", "missing-pack"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                shutil.copytree(ROOT / "shapes/aegis", root / "shapes/aegis")
                shutil.copytree(ROOT / "tests/fixtures/aegis-upstream", root / "tests/fixtures/aegis-upstream")
                if mutation == "constraint":
                    file = root / "shapes/aegis/homelab-infra.shapes.ttl"
                    graph = Graph().parse(file)
                    triple = next(graph.triples((None, SH.minCount, None)))
                    graph.remove(triple)
                    graph.serialize(file, format="turtle")
                elif mutation == "duplicate":
                    file = root / "shapes/aegis/placement.json"
                    data = json.loads(file.read_text())
                    data["shapes"].append(data["shapes"][0])
                    file.write_text(json.dumps(data))
                else:
                    (root / "shapes/aegis/personal-media.shapes.ttl").unlink()
                with self.assertRaises((ValueError, OSError)):
                    BUILDER.compatibility_bundle(root, SOURCE)

    def test_historical_keep_and_missing_code_claim_are_not_engine_authority(self):
        for mutation in ("domain-as-engine", "missing-claim", "changed-reference"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                shutil.copytree(ROOT / "shapes/aegis", root / "shapes/aegis")
                shutil.copytree(ROOT / "tests/fixtures/aegis-upstream", root / "tests/fixtures/aegis-upstream")
                path = root / "shapes/aegis/placement.json"
                placement = json.loads(path.read_text())
                engine = next(r for r in placement["shapes"] if r["shape"] == BUILDER.ENGINE_SHAPE)
                if mutation == "domain-as-engine":
                    directive = next(r for r in placement["shapes"] if r["shape"].endswith("/DirectiveShape"))
                    directive["file"] = engine["file"]
                elif mutation == "missing-claim":
                    engine.pop("engine_claim")
                else:
                    engine["engine_claim"]["path"] = "src/unrelated.rs"
                path.write_text(json.dumps(placement))
                with self.assertRaisesRegex(ValueError, "engine placement"):
                    BUILDER.compatibility_bundle(root, SOURCE)


if __name__ == "__main__":
    unittest.main()
