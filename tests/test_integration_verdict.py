"""The detector that says whether the integration suite ran must itself be tested.

aegis-d6mlhb is a bead about a job that could do nothing and report a green
check. Answering it with an unverified detector would reproduce the defect one
level up — a guard that looks installed and isn't — so every verdict here is
pinned to the exact log text that produces it, and the REAL suite's output is
used as the healthy fixture rather than a hand-written imitation of it.
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "integration_verdict", ROOT / "scripts/integration_verdict.py"
)
integration_verdict = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
sys.modules["integration_verdict"] = integration_verdict
SPEC.loader.exec_module(integration_verdict)

judge = integration_verdict.judge
MIN = integration_verdict.DEFAULT_MIN_TESTS
MAX_SKIP = integration_verdict.DEFAULT_MAX_SKIPPED

# Verbatim tail of `python3 -m unittest discover -s tests -v`, which is what
# `just test` runs. Kept exact: the detector reads unittest's wording, so a
# paraphrase would test the paraphrase.
HEALTHY = "----------------------------------------------------------------------\nRan 399 tests in 46.580s\n\nOK\n"


class DarkTests(unittest.TestCase):
    def test_a_missing_log_is_dark_and_not_a_pass(self):
        verdict = judge(None, MIN, MAX_SKIP)
        self.assertEqual("DARK", verdict.state)
        self.assertEqual(2, verdict.exit_code)

    def test_output_without_a_result_line_is_dark(self):
        # What a failed fetch leaves behind: the later steps never ran, so
        # nothing ever printed `Ran N tests`.
        verdict = judge("curl: (22) The requested URL returned error: 404\n", MIN, MAX_SKIP)
        self.assertEqual("DARK", verdict.state)
        self.assertEqual(2, verdict.exit_code)

    def test_dark_is_a_blocking_error(self):
        self.assertEqual("error", judge(None, MIN, MAX_SKIP).level)


class SkippedEverythingTests(unittest.TestCase):
    def test_all_skipped_exits_nonzero_though_unittest_exits_zero(self):
        # This is the exact state the bead names: unittest returns 0 here.
        log = "Ran 399 tests in 2.0s\n\nOK (skipped=399)\n"
        verdict = judge(log, MIN, MAX_SKIP)
        self.assertEqual("SKIPPED", verdict.state)
        self.assertEqual(1, verdict.exit_code)
        self.assertEqual("error", verdict.level)

    def test_the_single_dark_gate_that_was_actually_happening_is_caught(self):
        # The last healthy integration run reported `OK (skipped=1)`, and that
        # 1 was the SHACL certification test failing to see the CLI the job had
        # just downloaded. One skip must be enough to go red.
        verdict = judge("Ran 399 tests in 2.0s\n\nOK (skipped=1)\n", MIN, MAX_SKIP)
        self.assertEqual("UNHEALTHY", verdict.state)
        self.assertEqual(1, verdict.exit_code)


class FloorTests(unittest.TestCase):
    def test_a_collapse_in_the_collected_count_is_caught(self):
        verdict = judge("Ran 12 tests in 0.1s\n\nOK\n", MIN, MAX_SKIP)
        self.assertEqual("UNHEALTHY", verdict.state)
        self.assertIn("floor", verdict.detail)

    def test_adding_tests_never_trips_the_floor(self):
        # The floor is a ratchet, not a pin.
        self.assertEqual("RAN", judge("Ran 4000 tests in 9s\n\nOK\n", MIN, MAX_SKIP).state)

    def test_the_real_suites_output_is_healthy(self):
        # Control: the fixture the other arms are mutations OF must pass, or
        # every one of them proves nothing.
        verdict = judge(HEALTHY, MIN, MAX_SKIP)
        self.assertEqual("RAN", verdict.state)
        self.assertEqual(0, verdict.exit_code)
        self.assertEqual("notice", verdict.level)


class FailureTests(unittest.TestCase):
    def test_failures_and_errors_are_both_counted(self):
        verdict = judge("Ran 399 tests in 2s\n\nFAILED (failures=2, errors=1)\n", MIN, MAX_SKIP)
        self.assertEqual("UNHEALTHY", verdict.state)
        self.assertIn("3 test(s) failed", verdict.detail)


class ReportingTests(unittest.TestCase):
    def test_the_annotation_is_emitted_on_one_line(self):
        # A GitHub annotation is terminated by a newline, so a multi-line
        # message would truncate to its first line on the run page.
        import io

        stream = io.StringIO()
        integration_verdict.report(judge(None, MIN, MAX_SKIP), stream=stream, summary_path=None)
        first = stream.getvalue().splitlines()[0]
        self.assertTrue(first.startswith("::error::"))
        self.assertIn("DARK", first)

    def test_the_verdict_reaches_the_step_summary(self):
        import io

        with tempfile.TemporaryDirectory() as directory:
            summary = Path(directory) / "summary.md"
            integration_verdict.report(
                judge(HEALTHY, MIN, MAX_SKIP), stream=io.StringIO(), summary_path=str(summary)
            )
            self.assertIn("integration: RAN", summary.read_text())


class WorkflowGateTests(unittest.TestCase):
    """The existing test check must reject failed, skipped and cancelled gates."""

    def test_dependency_results_reach_the_existing_test_check(self):
        import os
        import subprocess
        import yaml

        jobs = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"]
        integration = jobs["integration"]
        self.assertFalse(integration.get("continue-on-error", False))
        suite = next(step for step in integration["steps"] if step.get("name") == "Run the suite")
        self.assertIn("just test", suite["run"])
        verdict = integration["steps"][-1]
        self.assertEqual("always()", verdict["if"])
        self.assertIn("integration_verdict.py", verdict["run"])
        gate = jobs["test"]
        self.assertEqual("always()", gate["if"])
        self.assertEqual({"integration", "memory-redirect-macos"}, set(gate["needs"]))
        step = gate["steps"][0]
        self.assertEqual("${{ needs.integration.result }}", step["env"]["INTEGRATION_RESULT"])
        self.assertEqual("${{ needs.memory-redirect-macos.result }}", step["env"]["MACOS_RESULT"])
        for live in ("success", "failure", "cancelled", "skipped", ""):
            for mac in ("success", "failure", "cancelled", "skipped", ""):
                with self.subTest(integration=live, macos=mac):
                    result = subprocess.run(["bash", "-c", step["run"]],
                                            env={**os.environ, "INTEGRATION_RESULT": live,
                                                 "MACOS_RESULT": mac}, capture_output=True)
                    self.assertEqual(live == mac == "success", result.returncode == 0)


class SelftestTests(unittest.TestCase):
    def test_the_scripts_own_selftest_passes(self):
        import contextlib
        import io

        with contextlib.redirect_stdout(io.StringIO()) as captured:
            code = integration_verdict._selftest()
        self.assertEqual(0, code)
        self.assertIn("all verdicts reachable", captured.getvalue())


if __name__ == "__main__":
    unittest.main()
