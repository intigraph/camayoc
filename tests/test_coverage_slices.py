#!/usr/bin/env python3
"""The coverage tool must count every slice the competency suite contains.

THE DEFECT THIS MAKES UNREPRESENTABLE
=====================================

This repo has now found the same defect three times, and twice in this exact
tool:

  * 2026-08-22 — `scripts/query_coverage.py` omitted the verification slice's
    own §D (cost accounting, Q16–21) from its denominator. Coverage read 5/13
    when it was 5/19.
  * 2026-08-25 — `SLICES` listed two of the suite's six files. Four slices,
    56 questions, had no coverage verdict at all; worse, four stored queries
    that genuinely answer `metrics-and-requirements` questions counted toward
    nothing, so the tool understated real coverage while appearing complete.
  * `docs/design/incident-corpus.md` §4.2 is a whole section about the same
    thing at a different scale, and `implemented-set.md` and `paper.md` both
    state the lesson in one line: **an uncounted question is a gap
    unreported.**

Restating the lesson is what did not work. The counting is now a property the
test suite enforces rather than a habit maintainers are asked to keep: a new
file in `competency/` cannot land without a coverage table, because this fails
the moment the two sets differ. That is the whole point of this file, and it
is deliberately the strictest kind of assertion — set equality, not a subset —
because the failure runs both ways. A slice with no table is unreported
coverage; a table with no slice is a denominator for questions nobody asks.
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    # Registered before exec: @dataclass resolves annotations through
    # sys.modules and fails with an opaque NoneType error without this.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


coverage = load("query_coverage", "scripts/query_coverage.py")
competency = load("competency", "scripts/competency.py")


class EverySliceIsCountedTests(unittest.TestCase):
    def test_the_coverage_tool_counts_exactly_the_slices_that_exist(self):
        """THE GUARD. Adding competency/<slice>.md without adding a coverage
        table for it fails here, before the missing verdict can be mistaken
        for a clean one."""
        on_disk = {p.stem for p in (ROOT / "competency").glob("*.md")}
        self.assertEqual(
            on_disk, set(coverage.SLICES),
            "competency/ and query_coverage.SLICES disagree. A slice on disk "
            "with no table is coverage nobody measures; a table with no slice "
            "is a denominator for questions nobody asks.",
        )

    def test_every_slice_reports_a_verdict(self):
        for name in coverage.SLICES:
            with self.subTest(slice=name):
                result = coverage.report(name)
                self.assertIn(result["verdict"], ("Empty", "Partial", "Full"))
                self.assertGreater(result["total"], 0)


class EveryQuestionIsCountedTests(unittest.TestCase):
    """The finer half of the same rule, and the one that bit first.

    Counting every FILE is not enough: §D was missing from a slice that was
    itself already counted. So each table's question numbers are checked
    against the questions the suite parser actually finds in that file.
    """

    def suite_numbers(self, name: str) -> set[int]:
        suite = competency.parse_suite(ROOT / "competency")
        return {q.number for q in suite if q.source == f"{name}.md"}

    def test_no_table_omits_a_question_the_slice_asks(self):
        for name in sorted(coverage.SLICES):
            with self.subTest(slice=name):
                missing = self.suite_numbers(name) - set(coverage.SLICES[name])
                self.assertEqual(
                    set(), missing,
                    f"{name}: questions {sorted(missing)} are asked by the "
                    "suite and absent from the coverage table. This is the "
                    "2026-08-22 §D defect exactly.",
                )

    def test_no_table_invents_a_question_the_slice_does_not_ask(self):
        """The other direction. A table row for a question that no longer
        exists inflates the denominator and quietly worsens every ratio."""
        for name in sorted(coverage.SLICES):
            with self.subTest(slice=name):
                invented = set(coverage.SLICES[name]) - self.suite_numbers(name)
                self.assertEqual(set(), invented, f"{name}: {sorted(invented)}")


class EveryGapIsActionableTests(unittest.TestCase):
    """Applied across all slices, not only the two that had tables.

    A gap reported without the terms it needs is a complaint rather than a
    finding: it gives whoever picks it up nothing to act on, and it is
    indistinguishable from a table row someone filled in to make a number
    look finished.
    """

    def test_every_gap_states_a_reason_and_what_it_would_need(self):
        for name in sorted(coverage.SLICES):
            for row in coverage.report(name)["rows"]:
                if row["state"] != "GAP":
                    continue
                with self.subTest(slice=name, question=row["question"]):
                    self.assertTrue(row["gap"].strip())
                    self.assertTrue(
                        row["needs"],
                        f"{name} Q{row['question']} names no required terms",
                    )

    def test_every_unwritten_row_says_why_it_is_expressible(self):
        """UNWRITTEN is a claim — that the ontology already carries the terms —
        and a claim with no reason cannot be checked by the next reader. It is
        also the flattering verdict of the two, so it is the one that needs the
        argument attached."""
        for name in sorted(coverage.SLICES):
            for row in coverage.report(name)["rows"]:
                if row["state"] != "UNWRITTEN":
                    continue
                with self.subTest(slice=name, question=row["question"]):
                    self.assertGreater(len(row["gap"].strip()), 20)
                    self.assertEqual([], row["needs"])

    def test_unwritten_is_never_counted_as_coverage(self):
        """The whole point of the split. A slice whose questions are all
        expressible and none stored answers nothing, and must not report a
        number that suggests otherwise."""
        result = coverage.report("crew-task-lifecycle")
        self.assertEqual(0, result["covered"])
        self.assertEqual("Empty", result["verdict"])
        self.assertGreater(result["unwritten"], 0)

    def test_no_slice_claims_a_query_that_is_absent_or_ungrounded(self):
        for name in sorted(coverage.SLICES):
            rows = coverage.report(name)["rows"]
            with self.subTest(slice=name):
                bad = [r for r in rows if r["state"] in ("MISSING", "UNGROUNDED")]
                self.assertEqual([], bad, bad)


class SuiteTotalTests(unittest.TestCase):
    """Pins the whole-suite figure, the way each slice's table pins its own.

    There was no such figure before 2026-08-25 because there was no
    denominator: two of six slices were counted, so the reportable number was
    16/19 and 12/16 — two ratios that between them describe 35 of the suite's
    91 questions and read, to anyone who did not check, as the suite.

    Moved 32 -> 40 on 2026-08-25 by camayoc-rkb: the workflow-and-archive
    slice went from 0/14 to 8/14. Seven of its rows were unwritten and one
    (Q7) was a gap that turned out to be this tool's own defect rather than
    the ontology's — the grounding check reported a correctly REUSED
    quipu-owned term as undefined. The gap count falls by one for that
    reason and only that reason; no gap was closed by minting.
    """

    def test_qname_detection_does_not_invent_predicates_inside_an_urn(self):
        self.assertEqual(set(), coverage.query_predicates("<urn:camayoc:workitem-burndown:delta>"))
        self.assertEqual({("camayoc", "unknown"), ("aegis", "Observation")},
                         coverage.query_predicates("camayoc:unknown ?s . ?s a aegis:Observation"))

    def test_the_suite_total_is_reported_and_pinned(self):
        # Event burn-down adds four: three stored, one external acceptance unwritten.
        # Priority adds seven questions: one stored, four unwritten, two gaps.
        # Delivery (aegis-rb3) adds seven gaps, all waiting on the proposed
        # aegis:DeploymentEvent class (Quipu proposal 31).
        figures = coverage.totals()
        self.assertEqual(9, figures["slices"])
        self.assertEqual(110, figures["total"])
        self.assertEqual(44, figures["covered"])
        self.assertEqual(28, figures["unwritten"])
        self.assertEqual(38, figures["gaps"])

    def test_the_three_states_account_for_every_question(self):
        """No question falls between the states. If one ever does it is
        invisible in every figure this tool prints, which is the defect."""
        figures = coverage.totals()
        self.assertEqual(
            figures["total"],
            figures["covered"] + figures["unwritten"] + figures["gaps"],
        )

    def test_the_denominator_matches_the_competency_suite_itself(self):
        """Cross-checked against the parser rather than against this file's own
        tables, so the two cannot agree on a wrong number."""
        suite = competency.parse_suite(ROOT / "competency")
        self.assertEqual(len(suite), coverage.totals()["total"])


if __name__ == "__main__":
    unittest.main()
