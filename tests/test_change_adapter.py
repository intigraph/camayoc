"""A recorded day's changes must reproduce the full population verdicts."""
import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import blocked_by as bb
import change_adapter as ca
import review_due as rd
from test_blocked_by import Store, item
from test_review_due import Store as ReviewStore

NOW = dt.datetime(2026, 10, 3, tzinfo=dt.timezone.utc).timestamp()


def change(subject, prop, value=None, old=None):
    return {"entity": bb.A + subject, "attribute": bb.term_iris(prop)[-1],
            "value": value, "old_value": old, "graph": "ROOT", "op": "assert"}


class Incremental(unittest.TestCase):
    def test_kind_change_is_subscribed_and_rejudges_only_changed_entity(self):
        self.assertIn(bb.A + "workKind", ca.description("review")["attributes"])
        store = ReviewStore([("f", "reviewAfter", '"2026-09-01"'),
                             ("f", "workKind", '"DreamCycle"')])
        result = ca.evaluate("review", store.post, {"now": NOW,
                             "changes": [change("f", "workKind", "DreamCycle")]})
        self.assertEqual(result["scope"], ["f"])
        self.assertEqual(result["records"][0]["work_kind"], "DreamCycle")

    def test_batched_history_matches_full_and_bounds_reads(self):
        triples = item("w", "open") + [("w", "blockedOn", "d")]
        for i in range(100):
            triples += item("d", "closed" if i == 99 else "open",
                            at=f"2026-09-{1 + i // 24:02d}T{i % 24:02d}:00:00Z", obs=f"od{i}")
        store = Store(triples)
        calls = []
        def post(endpoint, body):
            calls.append(body["query"])
            return store.post(endpoint, body)
        old = bb.evaluate(post, item="w")
        old_count = len(calls)
        calls.clear()
        new = bb.evaluate(post, item="w", batch_history=True)
        self.assertEqual(new, old)
        self.assertLess(len(calls), old_count // 3)
        self.assertTrue(any("?at31" in q for q in calls))
        self.assertFalse(any("VALUES" in q or "BIND" in q or " . " in q for q in calls))

    def test_batched_tie_preserves_projected_status_preference(self):
        store = Store(item("w", "open") + [("w", "blockedOn", "d"), ("d", "a", "WorkItem"),
                      ("d", "observes", "older"), ("d", "observes", "projected"),
                      ("older", "observedAt", "2026-09-30"), ("projected", "observedAt", "2026-09-30"),
                      ("projected", "observedStatus", "closed")])
        self.assertEqual(bb.evaluate(store.post, item="w", batch_history=True), bb.evaluate(store.post, item="w"))

    def test_idle_does_no_reads(self):
        def no_reads(*args):
            self.fail("idle adapter made a graph request")
        result = ca.evaluate("blocked", no_reads, {"now": NOW, "changes": [], "items": []})
        self.assertEqual(result["records"], [])

    def test_closed_dependency_invalidates_waiter_not_unrelated_population(self):
        store = Store(item("w", "open") + item("d", "closed", obs="od") + [("w", "blockedOn", "d")])
        result = ca.evaluate("blocked", store.post, {"now": NOW,
                             "changes": [change("od", "observedStatus", "closed")]})
        self.assertEqual(result["scope"], ["d", "w"])
        self.assertEqual(result["records"], bb.evaluate(store.post))

    def test_removed_observation_link_keeps_former_owner_in_scope(self):
        store = Store(item("w", "open") + item("d", "closed") + [("w", "blockedOn", "d")])
        result = ca.evaluate("blocked", store.post, {"now": NOW,
                             "changes": [change("d", "observes", old={"ref": bb.A + "deleted"})]})
        self.assertIn("w", result["scope"])

    def test_deleted_verification_link_invalidates_former_entity(self):
        store = ReviewStore([("f", "maxAge", "P1D")])
        result = ca.evaluate("review", store.post, {"now": NOW,
                             "changes": [change("v", "verifies", old={"ref": bb.A + "f"})]})
        self.assertEqual(result["scope"], ["f"])
        self.assertEqual(result["records"][0]["verdict"], "UNKNOWN")

    def test_missing_age_anchor_waits_for_graph_change(self):
        store = ReviewStore([("f", "maxAge", "P1D")])
        result = ca.evaluate("review", store.post, {"now": NOW, "items": ["f"]})
        self.assertEqual(result["records"][0]["verdict"], "UNKNOWN")
        self.assertIsNone(result["next_checks"]["f"])

    def test_due_without_graph_changes(self):
        store = ReviewStore([("f", "reviewAfter", '"2026-10-03T01:00:00Z"')])
        first = ca.evaluate("review", store.post, {"now": NOW, "items": ["f"]})
        due = first["next_checks"]["f"]
        self.assertEqual(due, NOW + 3600)
        after = ca.evaluate("review", store.post, {"now": due, "items": ["f"]})
        self.assertEqual(after["records"][0]["verdict"], "DUE")
        self.assertIsNone(after["next_checks"]["f"])

    def test_discovery_does_not_evaluate_population(self):
        store = Store(item("w", "open") + [("w", "blockedOn", "d")])
        calls = []
        def post(endpoint, body):
            calls.append(body["query"])
            return store.post(endpoint, body)
        result = ca.evaluate("blocked", post, {"discover": True, "now": NOW})
        self.assertEqual(result["items"], ["w"])
        self.assertEqual(len(calls), 2 * len(bb.graphs()))
        self.assertFalse(any("observedStatus" in q for q in calls))

    def test_catalogue_bounds_reverse_reads_instead_of_scanning_all_history(self):
        triples = []
        for i in range(70):
            triples += [("w", "observes", f"o{i}"), (f"o{i}", "observedBlockedOn", "d")]
        store = Store(triples)
        queries = []
        def post(endpoint, body):
            q = body["query"]
            if "SELECT ?w ?o" in q:
                return {"rows": [], "truncated": True}
            queries.append(q)
            return store.post(endpoint, body)
        result = ca.evaluate("blocked", post, {"now": NOW, "discover": True})
        self.assertEqual(result["items"], ["w"])
        self.assertEqual(len(queries), 5 * len(bb.graphs()))
        self.assertFalse(any("?w32" in q for q in queries))

    def test_recorded_day_parity_close_reopen_remove_and_add(self):
        store = Store(item("w", "open") + item("other", "open") + item("d", "open", obs="od")
                      + [("w", "blockedOn", "d"), ("other", "blockedOn", "d")])
        state = {r["item"]: r for r in bb.evaluate(store.post)}
        day = [("od", "observedStatus", "open", "closed"),
               ("od", "observedStatus", "closed", "open"),
               ("w", "blockedOn", "d", None),
               ("w", "blockedOn", None, "d"),
               ("od", "observedStatus", "open", "closed")]
        for step, (subject, prop, old, new) in enumerate(day):
            if old is not None:
                store.t.discard((subject, prop, old))
            if new is not None:
                store.t.add((subject, prop, new))
            result = ca.evaluate("blocked", store.post, {"now": NOW + step * 3600,
                                 "changes": [change(subject, prop, new, old)]})
            for key in result["scope"]:
                state.pop(key, None)
            state.update((r["item"], r) for r in result["records"])
            self.assertEqual(state, {r["item"]: r for r in bb.evaluate(store.post)}, (subject, prop, new))


class FailureHead(unittest.TestCase):
    """aegis-ztja: chaski keeps the head of stderr, so the cause must lead it."""

    def run_main(self, exc):
        import io
        from unittest import mock
        request = {"now": NOW, "changes": [change("weird name", "reviewAfter")] * 3}
        stderr = io.StringIO()
        with mock.patch.object(ca, "run", side_effect=exc), \
                mock.patch.object(sys, "stdin", io.StringIO(__import__("json").dumps(request))), \
                mock.patch.object(sys, "stderr", stderr):
            code = ca.main("directive")
        return code, stderr.getvalue()

    def test_unexpected_exception_is_named_in_the_first_150_bytes(self):
        code, err = self.run_main(ValueError("unsafe entity IRI"))
        self.assertEqual(code, 1)
        head = err.encode()[:150].decode(errors="replace")
        self.assertIn("ValueError: unsafe entity IRI", head)
        self.assertTrue(err.startswith("camayoc adapter: ValueError"))
        first = err.splitlines()[0]
        self.assertIn("kind=directive", first)
        self.assertIn("changes=3", first)
        self.assertIn("Traceback", err)

    def test_budget_exhaustion_keeps_exit_3(self):
        import planes
        code, err = self.run_main(planes.RequestBudgetExceeded("k=20"))
        self.assertEqual(code, 3)
        self.assertTrue(err.startswith("camayoc adapter: request budget exhausted"))


if __name__ == "__main__":
    unittest.main()


class IdleLimitRouting(unittest.TestCase):
    """aegis-qx96wr: tracker activity is an event for the review adapter, and an idle
    deadline is a stored next_check, so a lapse fires with NO scan and NO timer."""

    def store(self, triples):
        from sparql_fake import TripleStore
        from blocked_by import Q
        class S(ReviewStore):
            def post(self, endpoint, body):
                if body.get("graph"):
                    return {"rows": [], "truncated": False}
                return self.rows(TripleStore(self.t, rd.A, Q, {"verifies", "ownedBy", "observes"}).select(body["query"]))
        return S(triples)

    def obs(self, name, at, status="in_progress"):
        return [("w", "observes", name), (name, "observedAt", f'"{at}"'), (name, "observedStatus", f'"{status}"')]

    def test_new_tracker_observation_routes_its_work_item_and_sets_the_deadline(self):
        import datetime as dt
        at = dt.datetime.fromtimestamp(NOW, dt.timezone.utc).isoformat()
        store = self.store([("w", "idleLimit", '"P3D"')] + self.obs("o9", at))
        result = ca.evaluate("review", store.post, {"now": NOW, "changes": [change("o9", "observedAt")]})
        self.assertEqual(result["scope"], ["w"])
        self.assertEqual(result["records"][0]["verdict"], "NOT_DUE")
        self.assertEqual(result["next_checks"]["w"], NOW + 3 * 86400)
        # The stored deadline matures with no graph change: DUE, nothing further owed.
        later = ca.evaluate("review", store.post, {"now": NOW + 3 * 86400, "items": ["w"]})
        self.assertEqual(later["records"][0]["verdict"], "DUE")
        self.assertIsNone(later["next_checks"]["w"])

    def test_a_closed_item_has_no_deadline(self):
        import datetime as dt
        at = dt.datetime.fromtimestamp(NOW, dt.timezone.utc).isoformat()
        store = self.store([("w", "idleLimit", '"P3D"')] + self.obs("o9", at, "closed"))
        result = ca.evaluate("review", store.post, {"now": NOW, "changes": [change("o9", "observedStatus")]})
        self.assertEqual(result["records"][0]["verdict"], "NOT_DUE")
        self.assertIsNone(result["next_checks"]["w"])

    def test_a_work_item_with_no_idle_limit_yields_no_record(self):
        import datetime as dt
        at = dt.datetime.fromtimestamp(NOW, dt.timezone.utc).isoformat()
        store = self.store(self.obs("o9", at))
        result = ca.evaluate("review", store.post, {"now": NOW, "changes": [change("o9", "observedAt")]})
        self.assertEqual(result["records"], [])

    def test_the_subscription_names_the_activity_and_the_limit(self):
        attrs = ca.description("review")["attributes"]
        for term in ("idleLimit", "observedAt", "observedStatus"):
            self.assertTrue(any(a.endswith(term) for a in attrs), term)
