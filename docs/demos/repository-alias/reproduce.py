"""Offline archive verification demo; GitHub identity replies are mocked."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import types
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
PATH = "scripts/verify-repository-share.py"
BASE = "f6372f6e43e762385b0b573c2ca5432afff544c9"
spec = importlib.util.spec_from_file_location("fixture", ROOT / "scripts/test-repository-share-publication.py")
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
for label, code in (("before", subprocess.check_output(["git", "show", BASE + ":" + PATH], cwd=ROOT, text=True)),
                    ("after", (ROOT / PATH).read_text())):
    module = types.ModuleType("demo_" + label)
    module.__file__ = str(ROOT / PATH)
    exec(compile(code, module.__file__, "exec"), module.__dict__)
    fixture.verifier = module
    case = fixture.Publication("test_deterministic_archive_and_exact_readback")
    case.setUp()
    try:
        case.prepare()
        case.proof_path.write_text(json.dumps({**case.proof, "source_repository": "scbrown/camayoc"}))
        try:
            module.verify(case.root, "v1.2.3", case.revision)
            outcome = "ACCEPT declared alias and identical repository id"
        except ValueError:
            outcome = "REFUSE literal repository mismatch"
        print(label + ": " + outcome)
        assert outcome.startswith("ACCEPT" if label == "after" else "REFUSE")
        if label == "after":
            for control in ("missing-map", "wrong-id"):
                if control == "wrong-id":
                    case.identity_api.return_value.stdout = json.dumps({"id": 99, "full_name": "intigraph/camayoc"})
                with patch.dict(os.environ, {"REPOSITORY_SHARE_STACK_MAP": str(case.root / "absent")} if control == "missing-map" else {}):
                    try:
                        module.verify(case.root, "v1.2.3", case.revision)
                    except ValueError:
                        print(control + ": REFUSE")
                    else:
                        raise AssertionError("negative control accepted")
    finally:
        case.doCleanups()
print("PASS read-only fixture verification; no release assets or tags changed")
