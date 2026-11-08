"""Replay the two public fixtures through the actual producer and native checker."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from rdflib import Graph

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def run(*arguments):
    return subprocess.run(arguments, check=True, capture_output=True, text=True)


with tempfile.TemporaryDirectory() as directory:
    for producer, count in [("inventory", 3), ("terraform", 3)]:
        source = FIXTURES / f"{producer}.json"
        mapping = FIXTURES / f"{producer}.mapping.json"
        outputs = [Path(directory) / f"{producer}-{attempt}" for attempt in range(2)]
        for output in outputs:
            run(sys.executable, str(ROOT / "scripts/produce_infrastructure.py"),
                str(source), str(mapping), str(output))
            admitted = run(sys.executable, str(ROOT / "scripts/admit_infrastructure_output.py"),
                           str(output / "output.ttl"), str(output / "manifest.json"),
                           "--quipu", os.environ["QUIPU_BIN"])
            assert "ADMITTED_OFFLINE" in admitted.stdout
            assert "fixture-sentinel-never-emit" not in (output / "output.ttl").read_text()
            assert len(json.loads((output / "manifest.json").read_text())) == count
        first, second = [Graph().parse(output / "output.ttl", format="turtle") for output in outputs]
        assert set(first) == set(second)
        print(f"{producer}: records={count}; exact-batch/native SHACL=PASS; "
              "secret sentinel absent; repeat graph identical")
print("Fixture files only; no ingress or runtime activation.")
