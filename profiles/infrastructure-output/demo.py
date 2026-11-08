"""Record native SHACL's scope limit and exact-batch admission on one input."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SPEC = importlib.util.spec_from_file_location(
    "infrastructure_admission", ROOT / "scripts/admit_infrastructure_output.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
BINARY = os.environ["QUIPU_BIN"]
example = (HERE / "example.ttl").read_text()
expected = json.loads((HERE / "example.manifest.json").read_text())
print("Target: disposable source fixtures; no graph writes or deployed baseline")
for name in ["shapes.ttl", "example.ttl", "example.manifest.json"]:
    print(name, "sha256=" + hashlib.sha256((HERE / name).read_bytes()).hexdigest())
with tempfile.TemporaryDirectory() as directory:
    for label, body in [
        ("complete control", example),
        ("same input with profile marker removed", example.replace(
            " ; dct:conformsTo infra:ConnectorRecordShape", "")),
    ]:
        print("\nINPUT:", label)
        data = Path(directory) / "input.ttl"
        data.write_text(body)
        result = subprocess.run(
            [BINARY, "validate", "--shapes", str(HERE / "shapes.ttl"), "--data", str(data)],
            capture_output=True, text=True, check=False,
        )
        print("Native SHACL only:", result.stdout.strip())
        assert result.returncode == 0, "both inputs demonstrate native shape scope"
        try:
            count = MODULE.admit(MODULE.Graph().parse(data=body, format="turtle"), expected)
        except ValueError as error:
            print("Exact batch admission: REFUSED -", error)
            assert label != "complete control"
        else:
            print("Exact batch admission: ADMITTED_OFFLINE, records=", count)
            assert label == "complete control" and count == 1
print("\nPASS: complete control stays valid; omitted marker cannot bypass batch admission")
