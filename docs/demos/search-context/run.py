"""Replay the actual entity candidate parser at base and candidate; no network/model."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
BASE = "cd06b1b3e3ff19b8d064b0a785361dce61500023"
PROBE = r"""
import json, entity_link
row = {"entity":"urn:fixture:neighbor", "text":"reference subject", "similarity":0.8}
body = {"results":[row], "insufficient_context":True, "context_results":[],
        "context":{"contract":"quipu-context/v1", "outcome":"NO CONTEXT", "similarity_floor":0.52}}
negative = len(entity_link._entities(body))
body.update(insufficient_context=False, context_results=[row])
body["context"]["outcome"] = "CONTEXT"
positive = len(entity_link._entities(body))
try:
    entity_link._entities({"results":[row]})
    legacy = "CANDIDATES"
except ValueError:
    legacy = "UNKNOWN"
print(json.dumps({"no_context_candidates":negative,"positive_candidates":positive,"legacy":legacy}))
"""
for label, ref in [("base", BASE), ("candidate", "HEAD")]:
    revision = subprocess.check_output(["git", "rev-parse", ref], cwd=ROOT, text=True).strip()
    with tempfile.TemporaryDirectory() as directory:
        for name in ("entity_link.py", "jev.py", "search_context.py"):
            result = subprocess.run(["git", "show", f"{ref}:scripts/{name}"], cwd=ROOT, capture_output=True)
            if result.returncode == 0:
                (Path(directory) / name).write_bytes(result.stdout)
        print(label, revision, flush=True)
        subprocess.run([sys.executable, "-c", PROBE], cwd=directory, check=True)
