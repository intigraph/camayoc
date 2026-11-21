"""Same synthetic suite completion on base/candidate; no network or real spawn."""
import hashlib
import importlib.util
import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BASE = "b2845fcbb3eaac6a0c17ea5f4ccfde3c80b15fed"
sys.path.insert(0, str(ROOT / "scripts"))
fixture = json.loads((ROOT / "tests/fixtures/github/deliveries/check_run.completed.json").read_text())["body"]
run = fixture.pop("check_run")
fixture["check_suite"] = {k: v for k, v in run.items()
                           if k in ("node_id", "id", "head_sha", "status", "conclusion")}
fixture["check_suite"]["updated_at"] = "2026-11-21T12:00:00Z"
with tempfile.TemporaryDirectory() as directory:
    for label in ("base", "candidate"):
        source = (subprocess.check_output(["git", "show", BASE + ":scripts/github_ingress.py"], cwd=ROOT)
                  if label == "base" else (ROOT / "scripts/github_ingress.py").read_bytes())
        raw_mapping = (subprocess.check_output(["git", "show", BASE + ":mappings/github.json"], cwd=ROOT)
                       if label == "base" else (ROOT / "mappings/github.json").read_bytes())
        path = Path(directory) / (label + ".py")
        path.write_bytes(source)
        spec = importlib.util.spec_from_file_location(label, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        try:
            body, key = module.episode_for("check_suite", module.redact(fixture), actor="demo",
                                            mapping=json.loads(raw_mapping),
                                            mapping_sha=hashlib.sha256(raw_mapping).hexdigest())
        except module.Quarantined as error:
            assert label == "base"
            print(label, BASE, "REFUSED:", error)
        else:
            assert label == "candidate"
            print(label, "source-sha256=" + hashlib.sha256(source).hexdigest(),
                  "MAPPED:", body["nodes"][1]["type"], body["nodes"][0]["properties"]["githubEventKind"])
from github_recruitment import Attempts, Candidate, attempt
conn = sqlite3.connect(":memory:", isolation_level=None)
ledger = Attempts(conn)
candidate = Candidate("intigraph/widget", 42, "a" * 40, "review", "task-1")
spawned = []
for delivery in ("first", "duplicate-event"):
    result = attempt(ledger, candidate, delivery=delivery, event_at=1., admit=lambda _: None,
                     spawn=lambda c: spawned.append(c) or "fixture-receipt", clock=lambda: 2.)
    print("fixture transport", delivery, result)
assert len(spawned) == 1
print("Actual ledger readback:", conn.execute("SELECT state,receipt FROM recruitment_attempts").fetchall())
conn.close()
