#!/usr/bin/env python3
"""Replay actual archived responses and reject altered embedded requests offline."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import types
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import jev
import seed_schedule as candidate

BASE = "82066632011918d2844eb13718d1a746cdb042dc"
base = types.ModuleType("base_seed_schedule")
exec(compile(subprocess.check_output(["git", "show", BASE + ":scripts/seed_schedule.py"], cwd=ROOT), "base_seed_schedule.py", "exec"), base.__dict__)
folder = Path(__file__).parent
items = [json.loads(line) for line in (folder / "labels.jsonl").read_text().splitlines()]
records = [json.loads(line) for line in (folder / "run-20261104/responses.jsonl").read_text().splitlines()]
print("Offline request-binding repair; baseline", BASE)
with patch.object(jev, "JevClient", side_effect=AssertionError("network forbidden")):
    original = base.report(items, records)
    repaired = candidate.report(items, records)
    assert repaired["rows"] == original["rows"]
    print("Original archive: 32 unchanged rows, unknown=", repaired["unknown"], sep="")
    for field, value in (("state", {"title": "Unrelated request", "body": "Different input"}), ("questions", {})):
        changed = copy.deepcopy(records)
        changed[0]["response"]["request"][field] = value
        assert base.report(items, changed)["rows"] == original["rows"]
        print("BEFORE: altered", field, "accepted with original sidecar hash")
        try:
            candidate.report(items, changed)
        except ValueError as exc:
            print("AFTER: altered", field, "rejected:", exc)
        else:
            raise AssertionError("altered embedded request was accepted")
print("PASS: original data preserved; zero model requests; no adoption or installation claim")
