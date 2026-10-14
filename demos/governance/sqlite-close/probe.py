"""Native scratch-store repro; set DEMO_BASE_SOURCE to the base loader file."""
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from unittest.mock import patch

root = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(root / "tests"))
import integration_governance_loader as fixture

spec = importlib.util.spec_from_file_location("baseline_loader", os.environ["DEMO_BASE_SOURCE"])
before = importlib.util.module_from_spec(spec)
spec.loader.exec_module(before)
fixture.NativeLoaderTests.setUpClass()
try:
    for label, module in (("before", before), ("after", fixture.loader)):
        with tempfile.TemporaryDirectory(prefix="sqlite-close-demo-") as directory:
            handles = []
            connect = sqlite3.connect

            def retain(*args, **kwargs):
                connection = connect(*args, **kwargs)
                handles.append(connection)
                return connection

            options = fixture.NativeLoaderTests.fixture_options | {"output": Path(directory) / "receiver.db"}
            try:
                with patch.object(sqlite3, "connect", retain):
                    result = module.stage(**options)
                print(json.dumps({"revision": label, "outcome": result["outcome"],
                                  "direct_declarations": result["direct_declarations"]}), flush=True)
            except sqlite3.OperationalError as error:
                print(json.dumps({"revision": label, "error": type(error).__name__,
                                  "message": str(error)}), flush=True)
            finally:
                for connection in handles:
                    connection.close()
finally:
    fixture.NativeLoaderTests.doClassCleanups()
