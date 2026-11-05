"""review_due: a declared age is evaluated at read time, and "cannot tell" stays UNKNOWN (aegis-kxjack)."""
from __future__ import annotations

import datetime as dt
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import review_due as rd  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
from sparql_fake import TripleStore  # noqa: E402
from blocked_by import Q  # noqa: E402

A = rd.A
NOW = dt.datetime(2026, 9, 24, 12, 0, tzinfo=dt.timezone.utc)


class Store:
    """Triples (s, p, o) with local names; answers only single-pattern queries."""

    def __init__(self, triples):
        self.t = list(triples)

    def post(self, endpoint, body):
        if body.get("graph"):
            return {"rows": [], "truncated": False}
        return self.rows(TripleStore(self.t, A, Q, {"verifies", "ownedBy"}).select(body["query"]))

    @staticmethod
    def rows(r):
        return {"rows": r, "truncated": False}


def run(triples):
    return {r["entity"]: r for r in rd.evaluate(Store(triples).post, now=NOW)}


class ReviewAfter(unittest.TestCase):
    def test_lane_creation_clock_is_monotonic_with_out_of_order_ingress(self):
        triples = [("lane", "idleLimit", '"PT12H"'), ("lane", "workKind", '"DreamLane"'),
                   ("lane", "observes", "new"), ("new", "observedAt", '"2026-09-24T06:00:00Z"'),
                   ("new", "observedStatus", '"open"'), ("lane", "observes", "old"),
                   ("old", "observedAt", '"2026-09-23T00:00:00Z"'),
                   ("old", "observedStatus", '"open"')]
        lane = run(triples)["lane"]
        self.assertEqual(lane["verdict"], "NOT_DUE")
        self.assertEqual(lane["due_at"], "2026-09-24T18:00:00+00:00")
        self.assertEqual(lane["work_kind"], "DreamLane")

    def test_unique_work_kind_reaches_the_verdict_without_changing_event_identity(self):
        base = [("f", "reviewAfter", '"2026-09-20T00:00:00Z"')]
        plain = run(base)["f"]
        dream = run(base + [("f", "workKind", '"DreamCycle"')])["f"]
        self.assertEqual(dream["work_kind"], "DreamCycle")
        self.assertEqual(dream["event_id"], plain["event_id"])
        ambiguous = run(base + [("f", "workKind", '"DreamCycle"'),
                                ("f", "workKind", '"Directive"')])["f"]
        self.assertNotIn("work_kind", ambiguous)

    def test_a_passed_instant_is_due_and_names_its_owner(self):
        r = run([("f", "reviewAfter", '"2026-09-20T00:00:00Z"'), ("f", "ownedBy", "aegis:dearing")])["f"]
        self.assertEqual((r["verdict"], r["owner"]), ("DUE", "dearing"))

    def test_a_future_instant_is_not_due(self):
        self.assertEqual(run([("f", "reviewAfter", '"2026-10-01"')])["f"]["verdict"], "NOT_DUE")

    def test_an_unreadable_instant_is_unknown_not_fresh(self):
        self.assertEqual(run([("f", "reviewAfter", '"next tuesday"')])["f"]["verdict"], "UNKNOWN")


class MaxAge(unittest.TestCase):
    def verified(self, when):
        return [("v1", "verifies", "f"), ("v1", "verifiedAt", f'"{when}"')]

    def test_an_age_exceeded_since_the_last_verification_is_due(self):
        r = run([("f", "maxAge", '"P7D"')] + self.verified("2026-09-10T00:00:00Z"))["f"]
        self.assertEqual((r["verdict"], r["due_at"]), ("DUE", "2026-09-17T00:00:00+00:00"))

    def test_the_latest_verification_is_the_anchor(self):
        triples = [("f", "maxAge", '"P7D"'), ("v1", "verifies", "f"), ("v1", "verifiedAt", '"2026-09-01T00:00:00Z"'),
                   ("v2", "verifies", "f"), ("v2", "verifiedAt", '"2026-09-20T00:00:00Z"')]
        self.assertEqual(run(triples)["f"]["verdict"], "NOT_DUE")

    def test_an_unanchored_age_is_unknown_never_fresh_and_never_due(self):
        r = run([("f", "maxAge", '"P1W"')])["f"]
        self.assertEqual(r["verdict"], "UNKNOWN")
        self.assertIn("no anchor", r["basis"][0]["why"])

    def test_months_are_refused_as_ambiguous(self):
        self.assertIsNone(rd.parse_duration("P1M"))
        self.assertEqual(rd.parse_duration("PT1M"), dt.timedelta(minutes=1))
        self.assertEqual(rd.parse_duration("P2W"), dt.timedelta(weeks=2))
        self.assertIsNone(rd.parse_duration("P"))
        for bad in ("PT", "P1DT", "P1W2D", "P1H"):
            self.assertIsNone(rd.parse_duration(bad), bad)
        self.assertEqual(rd.parse_duration("P1DT2H30M"), dt.timedelta(days=1, hours=2, minutes=30))
        self.assertEqual(rd.parse_duration("PT2H"), dt.timedelta(hours=2))
        self.assertEqual(rd.parse_duration("P3D"), dt.timedelta(days=3))


class Combination(unittest.TestCase):
    def test_a_known_overdue_age_wins_over_an_unanchored_one(self):
        r = run([("f", "reviewAfter", '"2026-09-01"'), ("f", "maxAge", '"P7D"')])["f"]
        self.assertEqual(r["verdict"], "DUE")

    def test_all_known_and_ahead_is_not_due_but_one_unknown_is_unknown(self):
        self.assertEqual(run([("f", "reviewAfter", '"2026-12-01"'), ("f", "maxAge", '"P7D"')])["f"]["verdict"],
                         "UNKNOWN")

    def test_an_entity_with_no_declared_age_is_not_reported(self):
        self.assertEqual(run([("g", "ownedBy", "aegis:wu")]), {})

    def test_evidence_is_stable_and_moves_when_the_age_moves(self):
        a = run([("f", "reviewAfter", '"2026-09-20"')])["f"]["evidence"]
        self.assertEqual(a, run([("f", "reviewAfter", '"2026-09-20"')])["f"]["evidence"])
        self.assertNotEqual(a, run([("f", "reviewAfter", '"2026-09-21"')])["f"]["evidence"])


class DueEventId(unittest.TestCase):
    """sattler's ruling (aegis-2qo001), applied to the due event: a deterministic id."""

    def test_a_due_entity_gets_a_stable_event_id(self):
        a = run([("f", "reviewAfter", '"2026-09-20"')])["f"]
        b = run([("f", "reviewAfter", '"2026-09-20"')])["f"]
        self.assertEqual("DUE", a["verdict"])
        self.assertEqual(a["event_id"], b["event_id"])

    def test_a_new_declared_instant_is_a_new_event(self):
        a = run([("f", "reviewAfter", '"2026-09-20"')])["f"]["event_id"]
        self.assertNotEqual(a, run([("f", "reviewAfter", '"2026-09-21"')])["f"]["event_id"])

    def test_not_due_carries_no_event(self):
        self.assertNotIn("event_id", run([("f", "reviewAfter", '"2026-10-01"')])["f"])

    def deliver_with_a_crash(self, mint):
        received, checkpoint = set(), set()
        for attempt in range(2):
            key = mint(run([("f", "reviewAfter", '"2026-09-20"')])["f"])
            if key in checkpoint:
                continue
            received.add(key)
            if attempt:
                checkpoint.add(key)
        return received

    def test_a_crash_between_send_and_checkpoint_delivers_exactly_one(self):
        self.assertEqual(1, len(self.deliver_with_a_crash(lambda r: r["event_id"])))

    def test_SABOTAGE_a_send_time_uuid_would_deliver_two(self):
        import uuid
        self.assertEqual(2, len(self.deliver_with_a_crash(lambda r: str(uuid.uuid4()))))


class ShapeAndReaderAgree(unittest.TestCase):
    """The write must refuse exactly what the reader cannot use (sattler, review of #28)."""

    def test_the_shape_pattern_and_the_reader_accept_the_same_durations(self):
        shapes = (Path(__file__).resolve().parents[1] / "shapes" / "core.shapes.ttl").read_text()
        block = shapes[shapes.index("sh:path aegis:maxAge"):]
        pattern = re.search(r'sh:pattern "([^"]+)"', block).group(1).replace("\\\\", "\\")
        shape = re.compile(pattern)
        for text in ("P", "PT", "P1W", "P2D", "P1DT", "PT1H", "PT30M", "PT1H30M", "P1DT2H", "P1DT45M",
                     "P1DT2H30M", "P1W1D", "P1M", "P1H", "P1Y", "30 days", "P0D"):
            self.assertEqual(bool(shape.match(text)), rd.parse_duration(text) is not None, text)


class LifecycleHeartbeat(unittest.TestCase):
    """Shantytown owned-work lifecycle observations carry a liveness lease, not a review age."""

    HB = "owned-work-" + "a" * 64
    LAPSED = '"2026-09-20T00:00:00Z"'

    def facts(self, entity, kind='"observed"', source='"urn:shantytown:owned-work:abc"'):
        triples = [(entity, "reviewAfter", self.LAPSED), (entity, "ownedBy", "tim")]
        if kind:
            triples.append((entity, "sourceKind", kind))
        if source:
            triples.append((entity, "source", source))
        return triples

    def test_a_heartbeat_with_a_lapsed_lease_is_not_due(self):
        r = run(self.facts(self.HB))[self.HB]
        self.assertEqual(r["verdict"], "NOT_DUE")
        self.assertNotIn("event_id", r)
        self.assertEqual(r["basis"][0]["age"], "lifecycle-lease")

    def test_the_same_lapsed_instant_elsewhere_stays_due(self):
        r = run([("f", "reviewAfter", self.LAPSED), ("f", "ownedBy", "tim")])["f"]
        self.assertEqual(r["verdict"], "DUE")
        self.assertIn("event_id", r)

    def test_the_name_alone_is_not_enough(self):
        for kind, source in (('"observed"', '"urn:other:producer"'),
                             ('"declared"', '"urn:shantytown:owned-work:abc"'),
                             (None, '"urn:shantytown:owned-work:abc"'),
                             ('"observed"', None)):
            with self.subTest(kind=kind, source=source):
                self.assertEqual(run(self.facts(self.HB, kind, source))[self.HB]["verdict"], "DUE")

    def test_a_matching_producer_on_another_name_is_judged_normally(self):
        r = run(self.facts("other-fact"))["other-fact"]
        self.assertEqual(r["verdict"], "DUE")

    def test_two_sources_are_not_a_heartbeat(self):
        triples = self.facts(self.HB) + [(self.HB, "source", '"urn:other:producer"')]
        self.assertEqual(run(triples)[self.HB]["verdict"], "DUE")


if __name__ == "__main__":
    unittest.main()


class QuechuaDualRead(unittest.TestCase):
    """aegis-9dpcta: ages and owners are read under the legacy AND the quechua
    IRI. verifies/verifiedAt have no quechua twin, so they stay legacy."""

    def test_a_quechua_age_and_owner_are_read(self):
        r = run([("f", "q:reviewAfter", '"2026-09-20T00:00:00Z"'), ("f", "q:ownedBy", "aegis:dearing")])["f"]
        self.assertEqual((r["verdict"], r["owner"]), ("DUE", "dearing"))

    def test_a_quechua_max_age_anchors_on_a_legacy_verification(self):
        r = run([("f", "q:maxAge", '"P1D"'), ("v1", "verifies", "f"),
                 ("v1", "verifiedAt", '"2026-09-20T00:00:00Z"')])["f"]
        self.assertEqual(r["verdict"], "DUE")

    def test_the_same_age_under_both_iris_changes_nothing(self):
        legacy = run([("f", "reviewAfter", '"2026-09-20T00:00:00Z"')])["f"]
        dual = run([("f", "reviewAfter", '"2026-09-20T00:00:00Z"'),
                    ("f", "q:reviewAfter", '"2026-09-20T00:00:00Z"')])["f"]
        self.assertEqual(len(dual["basis"]), 1)
        self.assertEqual((dual["evidence"], dual.get("event_id")), (legacy["evidence"], legacy.get("event_id")))

    def test_conflicting_ages_are_both_kept_and_the_earliest_decides(self):
        r = run([("f", "reviewAfter", '"2026-12-01T00:00:00Z"'),
                 ("f", "q:reviewAfter", '"2026-09-20T00:00:00Z"')])["f"]
        self.assertEqual(len(r["basis"]), 2)
        self.assertEqual(r["verdict"], "DUE")

    def test_controls_absent_and_foreign_namespace_ages_are_not_read(self):
        self.assertEqual(run([("f", "x:reviewAfter", '"2026-09-20T00:00:00Z"')]), {})
        self.assertEqual(run([("f", "ownedBy", "aegis:dearing")]), {})


def run_obs(triples):
    """As run(), with `observes` an object property (WorkItem -> tracker Observation)."""
    class S(Store):
        def post(self, endpoint, body):
            if body.get("graph"):
                return {"rows": [], "truncated": False}
            return self.rows(TripleStore(self.t, A, Q, {"verifies", "ownedBy", "observes"}).select(body["query"]))
    return {r["entity"]: r for r in rd.evaluate(S(triples).post, now=NOW)}


class IdleLimit(unittest.TestCase):
    """aegis-qx96wr: a live WorkItem lapses when its tracker has been silent for idleLimit.

    The anchor is the LATEST tracker Observation's observedAt (activity pushes the
    expiry forward with no mutation); a latest Observation that says closed or
    deferred RESOLVES the obligation, so finished work never fires."""

    def obs(self, name, at, status="in_progress"):
        return [("w", "observes", name), (name, "observedAt", f'"{at}"'), (name, "observedStatus", f'"{status}"')]

    def test_silence_past_the_limit_is_due_from_the_last_activity(self):
        r = run_obs([("w", "idleLimit", '"P3D"')] + self.obs("o1", "2026-09-20T06:32:51Z"))["w"]
        self.assertEqual((r["verdict"], r["due_at"]), ("DUE", "2026-09-23T06:32:51+00:00"))
        self.assertTrue(r["event_id"].startswith("sha256:"))

    def test_recent_activity_is_not_due(self):
        self.assertEqual(run_obs([("w", "idleLimit", '"P3D"')] + self.obs("o1", "2026-09-23T00:00:00Z"))["w"]["verdict"],
                         "NOT_DUE")

    def test_the_latest_observation_is_the_anchor_not_the_first_listed(self):
        triples = [("w", "idleLimit", '"P3D"')] + self.obs("o2", "2026-09-23T00:00:00Z") + self.obs("o1", "2026-09-01T00:00:00Z")
        self.assertEqual(run_obs(triples)["w"]["verdict"], "NOT_DUE")

    def test_activity_moves_the_event_id(self):
        a = run_obs([("w", "idleLimit", '"P1D"')] + self.obs("o1", "2026-09-20T00:00:00Z"))["w"]
        b = run_obs([("w", "idleLimit", '"P1D"')] + self.obs("o1", "2026-09-20T00:00:00Z")
                    + self.obs("o2", "2026-09-22T00:00:00Z"))["w"]
        self.assertEqual((a["verdict"], b["verdict"]), ("DUE", "DUE"))
        self.assertNotEqual(a["event_id"], b["event_id"])

    def test_closed_or_deferred_resolves_and_never_fires(self):
        for status in ("closed", "deferred"):
            triples = [("w", "idleLimit", '"P1D"')] + self.obs("o1", "2026-09-01T00:00:00Z") \
                + self.obs("o2", "2026-09-02T00:00:00Z", status)
            r = run_obs(triples)["w"]
            self.assertEqual(r["verdict"], "NOT_DUE", status)
            self.assertIsNone(r["due_at"], status)
            self.assertIn("resolved", r["basis"][0]["why"])

    def test_no_observation_is_unknown_never_fresh(self):
        r = run_obs([("w", "idleLimit", '"P3D"')])["w"]
        self.assertEqual(r["verdict"], "UNKNOWN")

    def test_the_vj3uet_replay_fires_on_2026_10_03(self):
        # Measured on the seeds mirror: in_progress, last activity 2026-09-30T06:32:51Z.
        triples = [("w", "idleLimit", '"P3D"')] + self.obs("o1", "2026-09-30T06:32:51Z")
        before = {r["entity"]: r for r in rd.evaluate(
            type("S", (), {"post": staticmethod(lambda e, b: {"rows": [], "truncated": False})}).post, now=NOW)}
        self.assertEqual(before, {})
        r = run_obs(triples)
        self.assertEqual(r["w"]["due_at"], "2026-10-03T06:32:51+00:00")
