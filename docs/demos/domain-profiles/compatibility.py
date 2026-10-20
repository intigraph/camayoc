"""Demonstrate source-only compilation, exact parity and refusal without a store."""
import importlib.util
import json
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("bundle", ROOT / "scripts/build_aegis_compatibility.py")
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)
SOURCE = ROOT / "tests/fixtures/aegis-upstream/legacy-compatibility.shapes.ttl"

first = BUILDER.compatibility_bundle(ROOT, SOURCE)
second = BUILDER.compatibility_bundle(ROOT, SOURCE)
assert first == second
manifest = json.loads(first["manifest.json"])
with tempfile.TemporaryDirectory() as directory:
    wrong = Path(directory) / "wrong.ttl"
    wrong.write_bytes(SOURCE.read_bytes() + b"\n# not the pinned source\n")
    try:
        BUILDER.compatibility_bundle(ROOT, wrong)
    except ValueError as error:
        assert "source hash" in str(error)
    else:
        raise AssertionError("wrong source was accepted")
print(json.dumps({"before": "staged public targets require a compatibility composition",
                  "after": "original targets restored; complete source union isomorphic",
                  "original_source_sha256": manifest["source_sha256"],
                  "node_shapes": manifest["node_shapes"], "files": manifest["files"],
                  "repeated_bytes_identical": True, "wrong_source_refused": True,
                  "automatic_load": False, "runtime_acceptance": "NOT_MEASURED"}, sort_keys=True))
