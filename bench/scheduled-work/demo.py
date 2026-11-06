#!/usr/bin/env python3
"""Offline base/candidate demonstration; replay recorded responses, never call a model."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import jev_mcp
import seed_schedule

BASE = "088c403d7541eca1acad9a112aa8ab35d5410134"
base = types.ModuleType("base_jev_mcp")
base.__file__ = str(ROOT / "scripts/jev_mcp.py")
exec(compile(subprocess.check_output(["git", "show", BASE + ":scripts/jev_mcp.py"], cwd=ROOT), base.__file__, "exec"), base.__dict__)
labels = {row["id"]: row for row in map(json.loads, (Path(__file__).parent / "labels.jsonl").read_text().splitlines())}
responses = {row["id"]: row for row in map(json.loads, (Path(__file__).parent / "run-20261104/responses.jsonl").read_text().splitlines())}
print("Source checkout demo; base", BASE)
print("Candidate: scheduled-work adapter; actual recorded responses replayed, ZERO network calls")
for ident in ("case-09", "case-21"):
    item = labels[ident]
    print("\nINPUT", ident, json.dumps({k: item[k] for k in ("title", "body")}))
    def forbidden():
        raise AssertionError("base must not call a model")
    try:
        base.call_tool("check_scheduled_work", item, forbidden)
    except ValueError as exc:
        print("BEFORE:", str(exc))
    class RecordedClient:
        def ask(self, state, questions):
            out = responses[ident]["response"]
            assert out["request"]["state"] == state
            assert out["request"]["questions"] == questions
            return out
    with tempfile.TemporaryDirectory() as tmp:
        result = jev_mcp.call_tool("check_scheduled_work", item, RecordedClient,
                                  {"JEV_USAGE_LOG": str(Path(tmp) / "usage.jsonl")})
    print("AFTER:", json.dumps({k: result[k] for k in ("outcome", "labels_to_add", "sourceKind", "plane", "refuses_write")}))
    assert result["outcome"] == ("flag" if ident == "case-09" else "clear")
    assert result["refuses_write"] is False
print("\nPASS: cron flag + explicit Chaski trigger control; no tracker writes or installation claim")
