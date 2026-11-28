#!/usr/bin/env python3
"""Fail the integration gate unless the pinned live-store suite ran and passed.

The blocking CI job provides every optional dependency and permits zero skips.
A failed fetch or interrupted run must also fail: `if: always()` runs this
verdict even when no suite log was produced.

Exit codes: 0 healthy, 1 unhealthy or skipped, 2 missing/incomplete suite log.
Run with --selftest to prove every verdict is reachable.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

#: `unittest` prints this whenever it completes a run, even an all-skipped one.
_RAN = re.compile(r"^Ran (\d+) tests? in ", re.M)
_SKIPPED = re.compile(r"skipped=(\d+)")
_FAILURES = re.compile(r"(failures|errors)=(\d+)")

#: The suite collected 399 tests on 2026-09-05. The floor is a ratchet, not a
#: pin: adding tests is free, and REMOVING enough of them to fall through is
#: meant to require someone to lower this line on purpose. It exists because
#: `Ran N tests` was already being printed on every run and read on none, so a
#: collapse in N — an import error taking a whole module out of discovery, say —
#: looked exactly like a healthy green job.
DEFAULT_MIN_TESTS = 390

#: In the `integration` job every gate is satisfied: rdflib is pip-installed,
#: and the fetch exports QUIPU_BIN and QUIPU_SERVER_BIN, which is what
#: `tests/quipu_bin_guard.py` resolves. So a skip there is not a laptop being
#: reasonable, it is a gate that could not see the binary the job downloaded for
#: it — which is precisely how the SHACL certification test sat dark behind
#: `OK (skipped=1)`.
DEFAULT_MAX_SKIPPED = 0


class Verdict:
    def __init__(self, state, exit_code, level, headline, detail=""):
        self.state = state
        self.exit_code = exit_code
        self.level = level  # "notice" | "warning" | "error"
        self.headline = headline
        self.detail = detail

    def __repr__(self):  # pragma: no cover - debugging aid
        return f"Verdict({self.state}, exit={self.exit_code})"


def judge(log: str | None, min_tests: int, max_skipped: int) -> Verdict:
    """Decide what the suite output means. Pure: no I/O, so it is testable."""
    if log is None:
        return Verdict(
            "DARK", 2, "error",
            "integration DARK: the suite produced no log at all.",
            "The step that runs it did not get there — on this job that is "
            "almost always the quipu release fetch failing, which skips every "
            "later step. Check the `Fetch quipu-server` step. The pinned "
            "live-store suite must run before this gate can pass.",
        )

    match = _RAN.search(log)
    if match is None:
        return Verdict(
            "DARK", 2, "error",
            "integration DARK: the suite log has no `Ran N tests` line.",
            "unittest prints that line on every completed run, including an "
            "all-skipped one, so its absence means the run did not complete.",
        )

    ran = int(match.group(1))
    skipped = int(m.group(1)) if (m := _SKIPPED.search(log)) else 0
    broken = sum(int(count) for _, count in _FAILURES.findall(log))

    if ran and skipped >= ran:
        return Verdict(
            "SKIPPED", 1, "error",
            f"integration SKIPPED EVERYTHING: {skipped} of {ran} tests skipped.",
            "unittest exits 0 when every test skips, so this is the state that "
            "reads as a pass and is not one.",
        )

    problems = []
    if ran < min_tests:
        problems.append(
            f"only {ran} tests were collected, below the floor of {min_tests}. "
            "Either discovery lost a module, or tests were removed and the "
            "floor should be lowered deliberately."
        )
    if skipped > max_skipped:
        problems.append(
            f"{skipped} test(s) skipped, above the permitted {max_skipped}. In "
            "this job every gate's dependency is provided, so a skip means a "
            "gate could not see it — see tests/quipu_bin_guard.py."
        )
    if broken:
        problems.append(f"{broken} test(s) failed or errored.")

    if problems:
        return Verdict(
            "UNHEALTHY", 1, "error",
            f"integration ran {ran} tests but is not healthy.",
            " ".join(problems),
        )

    return Verdict(
        "RAN", 0, "notice",
        f"integration RAN: {ran} tests, {skipped} skipped, 0 failed.",
        "The suite exercised the fetched quipu binaries rather than skipping "
        "past them.",
    )


def report(verdict: Verdict, stream=sys.stdout, summary_path: str | None = None) -> None:
    """Emit the verdict as a GitHub annotation, a log line, and a summary row."""
    text = verdict.headline + (f" {verdict.detail}" if verdict.detail else "")
    # Surface the reason for the blocking failure on the run page.
    one_line = text.replace("\n", " ")
    print(f"::{verdict.level}::{one_line}", file=stream)
    print(text, file=stream)

    summary_path = summary_path if summary_path is not None else os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(f"### integration: {verdict.state}\n\n{text}\n\n")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", nargs="?", help="the `just test` output")
    parser.add_argument("--min-tests", type=int, default=DEFAULT_MIN_TESTS)
    parser.add_argument("--max-skipped", type=int, default=DEFAULT_MAX_SKIPPED)
    parser.add_argument("--selftest", action="store_true",
                        help="prove every verdict is reachable, then exit")
    args = parser.parse_args(argv)

    if args.selftest:
        return _selftest()

    if not args.log:
        parser.error("a log path is required unless --selftest is given")

    path = Path(args.log)
    log = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else None
    verdict = judge(log, args.min_tests, args.max_skipped)
    report(verdict)
    return verdict.exit_code


def _selftest() -> int:
    """Every verdict, reachable, with the input that produces it."""
    cases = [
        ("DARK (no log)", None, "DARK", 2),
        ("DARK (no result line)", "curl: (22) 404\n", "DARK", 2),
        ("SKIPPED everything", "Ran 399 tests in 1s\n\nOK (skipped=399)\n", "SKIPPED", 1),
        ("below the floor", "Ran 12 tests in 1s\n\nOK\n", "UNHEALTHY", 1),
        ("unexpected skip", "Ran 399 tests in 1s\n\nOK (skipped=1)\n", "UNHEALTHY", 1),
        ("real failure", "Ran 399 tests in 1s\n\nFAILED (failures=2)\n", "UNHEALTHY", 1),
        ("healthy", "Ran 399 tests in 1s\n\nOK\n", "RAN", 0),
    ]
    ok = True
    for name, log, expected_state, expected_exit in cases:
        verdict = judge(log, DEFAULT_MIN_TESTS, DEFAULT_MAX_SKIPPED)
        good = verdict.state == expected_state and verdict.exit_code == expected_exit
        ok &= good
        print(f"{'ok  ' if good else 'FAIL'}  {name:22}  -> {verdict.state} (exit {verdict.exit_code})")
    print("selftest:", "all verdicts reachable" if ok else "BROKEN")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
