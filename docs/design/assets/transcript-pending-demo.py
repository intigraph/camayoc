#!/usr/bin/env python3
"""Isolated lost-response fixture, actual publish() on base and head."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import types
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
BASE = "ca4164f84bd1c9ef80284ad064f7b3ce111018ef"
HEAD = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
record = dict(session="00000000-0000-0000-0000-000000000001", agent="example",
              harness="claude", at="", key="archive/example.md", sha256="a" * 64,
              bytes=10, tasks=["example-task"])

for revision, label in ((BASE, "BEFORE"), (HEAD, "AFTER")):
    module = types.ModuleType("producer")
    module.__file__ = str(ROOT / "scripts/ingest_transcript_sessions.py")
    source = subprocess.check_output(["git", "show", f"{revision}:scripts/ingest_transcript_sessions.py"], cwd=ROOT, text=True)
    exec(source, module.__dict__)
    session, artifact = module.iris(record)
    rows = [dict(s=artifact,p=module.ONTOLOGY+"contentSha256",o=record["sha256"]),
            dict(s=artifact,p=module.ONTOLOGY+"contentBytes",o="10"),
            dict(s=artifact,p=module.ONTOLOGY+"reference",o=record["key"]),
            dict(s=session,p=module.ONTOLOGY+"transcript",o=artifact),
            dict(s=artifact,p=module.ONTOLOGY+"transcriptOf",o=session),
            dict(s=session,p=module.ONTOLOGY+"declaredWorkItem",o=module.ONTOLOGY+"example-task")]
    calls = []
    def transport(path, payload, **kwargs):
        calls.append(path)
        if path == "/knot":
            raise OSError("fixture committed, response lost")
        return {"rows": [dict(subject=r["s"],predicate=r["p"],value=r["o"]) for r in rows]}
    print(f"{label} {revision}: identical isolated committed-POST/lost-response input", flush=True)
    with TemporaryDirectory() as tmp, patch.object(module.planes,"_post",side_effect=transport):
        state = Path(tmp) / "state.json"
        try:
            module.publish([record], "fixture", state, 1)
        except OSError as exc:
            print(f"first call: {exc}", flush=True)
        envelope = json.loads(state.with_suffix(".pending.json").read_text())
        print("journal fields:", sorted(envelope), flush=True)
        try:
            result = module.publish([record], "fixture", state, 1)
            print("recovery:", json.dumps(result, sort_keys=True), flush=True)
        except ValueError as exc:
            print("recovery refused:", str(exc).replace(tmp,"<fixture>"), flush=True)
        print("POST count:", calls.count("/knot"), "state committed:", state.exists(), flush=True)
        assert calls.count("/knot") == 1
        assert state.exists() == (label == "AFTER")
print("Expected before refusal and after no-resend recovery verified.", flush=True)
