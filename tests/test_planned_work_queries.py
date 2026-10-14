"""Read-time planned-work discovery: immutable tracker history is not current state."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from rdflib_guard import HAVE_RDFLIB, requires_rdflib

if HAVE_RDFLIB:
    import rdflib

ROOT = Path(__file__).resolve().parents[1]
A = "http://aegis.gastown.local/ontology/"
RECORDS = "https://camayoc.local/plane/crew/records"
FIRINGS = "urn:quipu:graph:root"


def stored(name):
    return json.loads((ROOT / "queries" / ("camayoc_" + name + ".json")).read_text())


def run(dataset, name, topic=""):
    query = stored(name)["template"].replace("{records}", RECORDS)
    # The store owns parameter escaping. This fixture only supplies literals.
    query = query.replace("{topic}", topic)
    # FROM names the supplied fixture contexts; an absent graph is empty,
    # never permission to fetch a namespace URL from the network.
    with patch("rdflib.plugins.sparql.SPARQL_LOAD_GRAPHS", False):
        rows = dataset.query(query)
        return [dict(zip((str(v) for v in rows.vars), row)) for row in rows]


@requires_rdflib
class PlannedWorkQueries(unittest.TestCase):
    def setUp(self):
        self.ds = rdflib.Dataset()
        self.records = self.ds.graph(rdflib.URIRef(RECORDS))
        self.firings = self.ds.graph(rdflib.URIRef(FIRINGS))
        self.item = rdflib.URIRef(A + "example-plan")
        self.records.add((self.item, rdflib.URIRef(A + "workKind"), rdflib.Literal("Directive")))

    def observation(self, suffix, at, status="in_progress", assignee="wu", kind=False):
        obs = rdflib.URIRef(A + "observation-" + suffix)
        value = {"assignee": assignee, "id": "example-plan", "status": status,
                 "title": "Review role for verification", "updated_at": at}
        if kind:
            value["kind"] = "Directive"
        self.records.add((self.item, rdflib.URIRef(A + "observes"), obs))
        if at is not None:
            self.records.add((obs, rdflib.URIRef(A + "observedAt"), rdflib.Literal(at)))
        self.records.add((obs, rdflib.URIRef(A + "observedStatus"), rdflib.Literal(status)))
        self.records.add((obs, rdflib.URIRef(A + "observedValue"),
                          rdflib.Literal(json.dumps(value, sort_keys=True, separators=(",", ":")))))
        return obs

    def firing(self, at="2026-10-03T06:32:00Z", focus=None):
        firing = rdflib.URIRef(A + "firing-example")
        for prop, obj in [("firedBy", rdflib.URIRef(A + "reaction-entity-review-due")),
                          ("focus", focus or self.item),
                          # Quipu preserves the producer's UTC-Z lexical value.
                          # rdflib otherwise rewrites it to an offset spelling.
                          ("startedAt", rdflib.Literal(at, datatype=rdflib.XSD.dateTime,
                                                      normalize=False))]:
            self.firings.add((firing, rdflib.URIRef(A + prop), obj))

    def test_current_plan_has_status_owner_and_recorded_topic(self):
        self.observation("current", "2026-09-30T06:32:00.000000001Z")
        self.records.add((self.item, rdflib.URIRef(A + "about"), rdflib.URIRef(A + "lead")))
        row, = run(self.ds, "open_plans", "review")
        self.assertEqual(str(row["status"]), "in_progress")
        self.assertEqual(str(row["owner"]), "wu")
        self.assertEqual(str(row["about"]), A + "lead")
        self.assertEqual(str(row["ownerState"]), "assigned")

    def test_latest_closed_or_deferred_never_revives_old_open_snapshot(self):
        for status in ["closed", "deferred"]:
            with self.subTest(status=status):
                self.setUp()
                self.observation("old", "2026-09-30T06:32:00Z", "open")
                self.observation("new", "2026-10-01T06:32:00Z", status)
                self.assertEqual(run(self.ds, "open_plans"), [])

    def test_reopened_latest_snapshot_is_open(self):
        self.observation("old", "2026-09-30T06:32:00Z", "closed")
        self.observation("new", "2026-10-01T06:32:00Z", "open")
        self.assertEqual(str(run(self.ds, "open_plans")[0]["status"]), "open")

    def test_fractional_precision_is_normalized_before_latest_selection(self):
        self.observation("older", "2026-10-01T06:32:00.5Z", "closed")
        self.observation("newer", "2026-10-01T06:32:00.500000001Z", "open")
        row, = run(self.ds, "open_plans")
        self.assertEqual(str(row["status"]), "open")
        self.assertEqual(str(row["observedAt"]), "2026-10-01T06:32:00.500000001Z")

    def test_equivalent_kind_backfill_snapshots_do_not_create_ambiguity(self):
        self.observation("before", "2026-10-01T06:32:00Z")
        self.observation("after", "2026-10-01T06:32:00Z", kind=True)
        self.assertEqual(len(run(self.ds, "open_plans")), 1)

    def test_conflicting_snapshots_at_same_instant_abstain(self):
        for change in [{"status": "closed"}, {"assignee": "ellie"}]:
            with self.subTest(change=change):
                self.setUp()
                self.observation("first", "2026-10-01T06:32:00Z")
                self.observation("second", "2026-10-01T06:32:00Z", **change)
                self.assertEqual(run(self.ds, "open_plans"), [])

    def test_invalid_offset_or_undated_history_is_not_false_current_state(self):
        for at in ["not-an-instant", "2026-10-01T06:32:00+00:00", None,
                   "2026-02-30T06:32:00Z", "2026-04-31T06:32:00Z",
                   "2026-13-01T06:32:00Z", "2026-10-01T24:32:00Z",
                   "0000-01-01T06:32:00Z"]:
            with self.subTest(at=at):
                self.setUp()
                self.observation("good", "2026-10-01T06:32:00Z")
                self.observation("unorderable", at)
                self.assertEqual(run(self.ds, "open_plans"), [])

    def test_null_and_escaped_owner_are_distinct(self):
        for owner, expected in [(None, "unassigned"), ("", "unassigned"),
                                ('name"withquote', "unknown")]:
            with self.subTest(owner=owner):
                self.setUp()
                self.observation("current", "2026-10-01T06:32:00Z", assignee=owner)
                row, = run(self.ds, "open_plans")
                self.assertEqual(str(row["ownerState"]), expected)
                self.assertEqual(str(row["owner"]), "")

    def test_topic_is_case_insensitive_substring_not_a_semantic_guess(self):
        self.observation("current", "2026-10-01T06:32:00Z")
        self.assertEqual(len(run(self.ds, "open_plans", "REVIEW")), 1)
        self.assertEqual(run(self.ds, "open_plans", "unrelated"), [])

    def test_valid_leap_dates_are_supported_and_false_leaps_abstain(self):
        for at, count in [("2024-02-29T06:32:00Z", 1),
                          ("2000-02-29T06:32:00Z", 1),
                          ("1900-02-29T06:32:00Z", 0),
                          ("2026-02-29T06:32:00Z", 0)]:
            with self.subTest(at=at):
                self.setUp()
                self.observation("current", at)
                self.assertEqual(len(run(self.ds, "open_plans")), count)

    def test_ordinary_work_and_dream_cycles_are_outside_plan_query(self):
        self.observation("current", "2026-10-01T06:32:00Z")
        self.records.set((self.item, rdflib.URIRef(A + "workKind"), rdflib.Literal("DreamCycle")))
        self.assertEqual(run(self.ds, "open_plans"), [])

    def test_directive_counterfactual_flags_october_third_without_now(self):
        self.observation("current", "2026-09-30T06:32:00Z")
        self.firing()
        row, = run(self.ds, "lapsed_directives")
        self.assertEqual(str(row["owner"]), "wu")
        self.assertIn("2026-10-03", str(row["startedAt"]))

    def test_old_firing_is_excluded_after_activity_closure_or_deferral(self):
        for status in ["open", "in_progress", "closed", "deferred"]:
            with self.subTest(status=status):
                self.setUp()
                self.observation("old", "2026-09-30T06:32:00Z")
                self.firing()
                self.observation("fresh", "2026-10-04T06:32:00Z", status)
                self.assertEqual(run(self.ds, "lapsed_directives"), [])

    def test_unrelated_firing_and_no_firing_are_not_lapses(self):
        self.observation("current", "2026-09-30T06:32:00Z")
        self.assertEqual(run(self.ds, "lapsed_directives"), [])
        self.firing(focus=rdflib.URIRef(A + "another-item"))
        self.assertEqual(run(self.ds, "lapsed_directives"), [])

    def test_wrong_projection_graph_cannot_supply_a_current_snapshot(self):
        self.observation("current", "2026-09-30T06:32:00Z")
        self.assertEqual(run(rdflib.Dataset(), "open_plans"), [])


if __name__ == "__main__":
    unittest.main()
