"""A missing-dependency environment must refuse before review can report success."""

import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ReviewPreflightTests(unittest.TestCase):
    def test_missing_site_packages_refuse_without_success_output(self):
        preflight = subprocess.run(
            [sys.executable, "-S", str(ROOT / "scripts/review_preflight.py")],
            capture_output=True, text=True,
        )
        self.assertNotEqual(preflight.returncode, 0)
        self.assertIn("preflight refused", preflight.stderr)
        self.assertIn("rdflib", preflight.stderr)
        self.assertIn("pyshacl", preflight.stderr)
        self.assertEqual(preflight.stdout, "")

    def test_existing_ci_guard_still_refuses_missing_rdflib(self):
        process = subprocess.run(
            [sys.executable, "-S", "-m", "unittest", "discover", "-s", "tests",
             "-p", "test_dual_read_queries.py"],
            cwd=ROOT, env={**os.environ, "CI": "1"}, capture_output=True, text=True,
        )
        self.assertNotEqual(process.returncode, 0)
        self.assertIn("rdflib is not installed", process.stderr)
        self.assertIn("FAILED (failures=2)", process.stderr)


if __name__ == "__main__":
    unittest.main()
