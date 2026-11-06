#!/usr/bin/env python3
"""Compare the actual verifier's download target without network or credentials."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[3]
BASE = "7162eaa0"
os.environ["GITHUB_REPOSITORY"] = "intigraph/camayoc"


def probe(script, label):
    spec = importlib.util.spec_from_file_location(label, script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    targets = []

    def download(args, **kwargs):
        targets.append(args[args.index("--repo") + 1])
        return subprocess.CompletedProcess(args, 1, "", "synthetic: no assets")

    try:
        module.wait("v1.2.3", "1" * 40, attempts=1, run=download)
    except ValueError:
        pass  # The synthetic missing asset must still refuse.
    assert len(targets) == 1
    print(label + ": GITHUB_REPOSITORY=intigraph/camayoc -> download --repo " + targets[0])
    return targets[0]


with tempfile.TemporaryDirectory(prefix="repository-identity-demo-") as temporary:
    old = Path(temporary)
    for name in ("verify-repository-share.py", "publish_repository_share.py"):
        (old / name).write_bytes(subprocess.check_output(
            ["git", "-C", str(ROOT), "show", BASE + ":scripts/" + name]))
    assert probe(old / "verify-repository-share.py", "before") == "scbrown/camayoc"
    assert probe(ROOT / "scripts/verify-repository-share.py", "after") == "intigraph/camayoc"
print("Both probes refuse the missing synthetic asset; no network or publication occurred.")
