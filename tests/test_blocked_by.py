"""blocked_by: whether a blocker still holds is evaluated at read time (aegis-c0awwp).

A small in-memory store answers the evaluator's single-pattern queries. The
arm that matters most: "could not tell" never rounds to UNBLOCKED, because
that is the direction an event consumer acts on.
"""
from __future__ import annotations

import datetime as dt
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import blocked_by as bb  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
from sparql_fake import TripleStore  # noqa: E402

A = bb.A
TODAY = dt.date(2026, 9, 24)


# Predicates whose objects are instances (returned as aegis:<name>).
IRI_OBJECTS = {"blockedOn", "observes", "observedBlockedOn"}


class Store:
    """Triples as (s, p, o) with local names for aegis terms."""

    def __init__(self, triples, asks=None):
        self.t = set(triples)
        self.asks = asks or {}

    def post(self, endpoint, body):
        q = body["query"]
        if body.get("graph"):
            return {"rows": [], "truncated": False}  # everything lives in default here
        if q.startswith("ASK"):
            if q in self.asks and isinstance(self.asks[q], Exception):
                raise self.asks[q]
            return {"result": self.asks.get(q, False)}
        return {"rows": self._select(q), "truncated": False}

    def _select(self, q):
        return TripleStore(self.t, A, bb.Q, IRI_OBJECTS).select(q)


def item(name, status=None, at="2026-09-20T00:00:00Z", obs=None):
    out = [(name, "a", "WorkItem")]
    if status:
        o = obs or f"obs-{name}-{at}"
        out += [(name, "observes", o), (o, "observedAt", at), (o, "observedStatus", status)]
    return out


def run(store, **kw):
    kw.setdefault("probes", {})
    return {r["item"]: r for r in bb.evaluate(store.post, today=TODAY, **kw)}


class WorkItemTargets(unittest.TestCase):
    def test_all_dependencies_closed_is_unblocked(self):
        s = Store(item("w", "blocked") + item("d1", "closed") + [("w", "blockedOn", "d1")])
        self.assertEqual(run(s)["w"]["verdict"], "UNBLOCKED")

    def test_the_latest_observation_wins(self):
        s = Store(item("w", "blocked") + item("d", "open", at="2026-09-01T00:00:00Z", obs="o1")
                  + item("d", "closed", at="2026-09-23T00:00:00Z", obs="o2")
                  + [("w", "blockedOn", "d")])
        self.assertEqual(run(s)["w"]["verdict"], "UNBLOCKED")

    def test_one_open_dependency_is_blocked(self):
        s = Store(item("w", "blocked") + item("d1", "closed") + item("d2", "in_progress")
                  + [("w", "blockedOn", "d1"), ("w", "blockedOn", "d2")])
        self.assertEqual(run(s)["w"]["verdict"], "BLOCKED")

    def test_closedAt_on_the_workitem_counts_as_closed(self):
        s = Store(item("w", "open") + item("d") + [("d", "closedAt", "2026-09-22"), ("w", "blockedOn", "d")])
        self.assertEqual(run(s)["w"]["verdict"], "UNBLOCKED")

    def test_unknown_never_rounds_to_unblocked(self):
        s = Store(item("w", "open") + item("d1", "closed") + item("d2")  # d2: no status at all
                  + [("w", "blockedOn", "d1"), ("w", "blockedOn", "d2")])
        self.assertEqual(run(s)["w"]["verdict"], "UNKNOWN")

    def test_a_closed_item_is_left_out(self):
        s = Store(item("w", "closed") + item("d1", "open") + [("w", "blockedOn", "d1")])
        self.assertNotIn("w", run(s))


class TrackerProjection(unittest.TestCase):
    """aegis-3b3nrb: tracker dependencies live on the versioned Observation."""

    def obs(self, item, name, at, status, deps=()):
        return ([(item, "observes", name), (name, "observedAt", at), (name, "observedStatus", status)]
                + [(name, "observedBlockedOn", d) for d in deps])

    def test_a_projected_dependency_blocks(self):
        s = Store([("w", "a", "WorkItem")] + self.obs("w", "o1", "2026-09-20", "blocked", ["d"])
                  + item("d", "open"))
        self.assertEqual(run(s)["w"]["verdict"], "BLOCKED")

    def test_a_dependency_removed_in_the_newest_observation_no_longer_blocks(self):
        s = Store([("w", "a", "WorkItem")]
                  + self.obs("w", "o1", "2026-09-20", "blocked", ["d"])  # older: blocked on d
                  + self.obs("w", "o2", "2026-09-23", "open", [])         # newest: dependency gone
                  + item("d", "open"))
        self.assertNotIn("w", run(s), "an item with no current blocker is not reported")

    def test_a_same_instant_pre_projection_observation_does_not_win(self):
        # Measured on aegis-sfpfwf: the tracker state was observed before the
        # projection landed ("zz", no status, no deps) and again after it
        # ("aa"), with the SAME observedAt. The name used to decide the tie,
        # and "zz" won, so the item read as having no blockers.
        s = Store([("w", "a", "WorkItem"), ("w", "observes", "zz"), ("zz", "observedAt", "2026-09-20")]
                  + self.obs("w", "aa", "2026-09-20", "blocked", ["d"])
                  + item("d", "open"))
        self.assertEqual(run(s)["w"]["verdict"], "BLOCKED")

    def test_one_item_reads_only_its_latest_observation(self):
        s = Store([("w", "a", "WorkItem")] + self.obs("w", "o1", "2026-09-20", "blocked", ["d"])
                  + item("d", "open"))
        self.assertEqual(run(s, item="w")["w"]["verdict"], "BLOCKED")

    def test_status_falls_back_to_the_observation_snapshot(self):
        s = Store(item("w", "open") + [("d", "a", "WorkItem"), ("d", "observes", "od"),
                  ("od", "observedAt", "2026-09-22"), ("od", "observedValue", '{"status": "closed"}'),
                  ("w", "blockedOn", "d")])
        self.assertEqual(run(s)["w"]["verdict"], "UNBLOCKED")


class HistoryReadBudget(unittest.TestCase):
    def test_history_growth_has_a_linear_request_budget(self):
        for size in (5, 10, 20, 40):
            with self.subTest(history=size):
                triples = item("dependency", "open")
                for i in range(size):
                    stamp = (dt.datetime(2026, 1, 1) + dt.timedelta(hours=i)).isoformat() + "Z"
                    triples += item("work", "open", at=stamp, obs=f"o{i}")
                    triples.append((f"o{i}", "observedBlockedOn", "dependency"))
                store, requests = Store(triples), []
                def post(endpoint, body):
                    requests.append(body)
                    return store.post(endpoint, body)
                result = bb.evaluate(post, probes={})
                self.assertEqual(result[0]["verdict"], "BLOCKED")
                self.assertLessEqual(len(requests), 12 * size + 60)

    def test_the_next_evaluation_reads_new_history(self):
        store = Store(item("work", "open") + item("dependency", "open")
                      + [("work", "blockedOn", "dependency")])
        self.assertEqual(run(store)["work"]["verdict"], "BLOCKED")
        store.t.update(item("dependency", "closed", at="2026-09-25T00:00:00Z"))
        self.assertEqual(run(store)["work"]["verdict"], "UNBLOCKED")


class TypedProbes(unittest.TestCase):
    """pr-merged / release-installed / ci-green: the parameters come from the
    graph, the probe code is fixed in camayoc (aegis-c0awwp)."""

    def case(self, kind, props, answer):
        calls = []
        def probe(*args):
            calls.append(args)
            return answer
        s = Store(item("w", "open") + [("b", "a", "Blocker"), ("b", "blockerKind", kind)]
                  + [("b", k, v) for k, v in props.items()] + [("w", "blockedOn", "b")])
        return run(s, probes={kind: probe})["w"]["verdict"], calls

    def test_each_probe_gets_its_parameters_and_its_answer_decides(self):
        for kind, props, want_args in (
                ("pr-merged", {"prRef": "scbrown/quipu#274"}, ("scbrown/quipu#274",)),
                ("release-installed", {"tool": "yupana", "minVersion": "0.10.0"}, ("yupana", "0.10.0")),
                ("ci-green", {"repoRef": "scbrown/quipu@main"}, ("scbrown/quipu@main",))):
            for answer, want in ((True, "UNBLOCKED"), (False, "BLOCKED"), (None, "UNKNOWN")):
                with self.subTest(kind=kind, answer=answer):
                    verdict, calls = self.case(kind, props, answer)
                    self.assertEqual(verdict, want)
                    self.assertEqual(calls, [want_args])

    def test_a_missing_parameter_is_unknown_and_nothing_is_probed(self):
        verdict, calls = self.case("pr-merged", {}, True)
        self.assertEqual((verdict, calls), ("UNKNOWN", []))

    def test_the_real_probes_refuse_malformed_parameters_without_running_anything(self):
        self.assertIsNone(bb.probe_pr_merged("quipu 274; rm -rf ~"))
        self.assertIsNone(bb.probe_release_installed("yupana; rm -rf ~", "0.10.0"))
        self.assertIsNone(bb.probe_release_installed("yupana", "latest"))
        self.assertIsNone(bb.probe_ci_green("not a repo ref"))

    def test_version_comparison_pads_and_orders_numerically(self):
        self.assertEqual(bb._version_tuple("yupana 0.10.0 (abc)"), (0, 10, 0))
        orig = bb._run
        try:
            bb._run = lambda argv: "yupana 0.9.12\n"
            self.assertFalse(bb.probe_release_installed("yupana", "0.10"))
            bb._run = lambda argv: "yupana 0.10.0\n"
            self.assertTrue(bb.probe_release_installed("yupana", "0.10"))
            bb._run = lambda argv: None
            self.assertIsNone(bb.probe_release_installed("yupana", "0.10"))
        finally:
            bb._run = orig


class Planes(unittest.TestCase):
    def test_declared_blockers_count_and_inferred_ones_do_not(self):
        import planes
        gs = bb.graphs()
        self.assertIn(planes.plane_for("declared"), gs)
        self.assertIn(planes.plane_for("observed"), gs)
        self.assertNotIn(planes.plane_for("inferred"), gs)


class ConditionTargets(unittest.TestCase):
    def blocker(self, name, **props):
        return [(name, "a", "Blocker")] + [(name, k, v) for k, v in props.items()]

    def test_a_date_blocker_resolves_on_its_date(self):
        past = Store(item("w", "open") + self.blocker("b", resolvesOn='"2026-09-24"^^xsd:date')
                     + [("w", "blockedOn", "b")])
        future = Store(item("w", "open") + self.blocker("b", resolvesOn='"2026-09-25"^^xsd:date')
                       + [("w", "blockedOn", "b")])
        self.assertEqual(run(past)["w"]["verdict"], "UNBLOCKED")
        self.assertEqual(run(future)["w"]["verdict"], "BLOCKED")

    def test_an_ask_true_resolves_and_false_blocks(self):
        ask = "ASK { ?r a <x> }"
        for answer, want in ((True, "UNBLOCKED"), (False, "BLOCKED")):
            s = Store(item("w", "open") + self.blocker("b", resolutionQuery=ask)
                      + [("w", "blockedOn", "b")], asks={ask: answer})
            self.assertEqual(run(s)["w"]["verdict"], want)

    def test_an_ask_that_fails_is_unknown_not_resolved(self):
        ask = "ASK { broken"
        s = Store(item("w", "open") + self.blocker("b", resolutionQuery=ask)
                  + [("w", "blockedOn", "b")], asks={ask: RuntimeError("HTTP 400")})
        self.assertEqual(run(s)["w"]["verdict"], "UNKNOWN")

    def test_a_blocker_with_no_resolution_is_unknown(self):
        s = Store(item("w", "open") + self.blocker("b") + [("w", "blockedOn", "b")])
        self.assertEqual(run(s)["w"]["verdict"], "UNKNOWN")

    def test_one_item_by_id(self):
        s = Store(item("w", "open") + item("d", "closed") + item("v", "open") + item("e", "open")
                  + [("w", "blockedOn", "d"), ("v", "blockedOn", "e")])
        self.assertEqual(list(run(s, item="w")), ["w"])


class AdapterRecord(unittest.TestCase):
    """aegis-2qo001: what the emitter keys transitions and delivery on."""

    def base(self, dep_status="open"):
        return (item("w", "blocked") + item("d", dep_status) + [("w", "blockedOn", "d"),
                ("obs-w-2026-09-20T00:00:00Z", "observedValue", '{"status": "blocked", "assignee": "grant"}')])

    def test_the_record_names_item_verdict_evidence_assignee_and_targets(self):
        r = run(Store(self.base()))["w"]
        self.assertEqual((r["verdict"], r["assignee"]), ("BLOCKED", "grant"))
        self.assertEqual([b["target"] for b in r["blockers"]], ["d"])
        self.assertTrue(r["evidence"].startswith("sha256:"))

    def test_the_same_facts_give_the_same_evidence_on_every_run(self):
        self.assertEqual(run(Store(self.base()))["w"]["evidence"], run(Store(self.base()))["w"]["evidence"])

    def test_a_blocker_changing_state_changes_the_evidence(self):
        self.assertNotEqual(run(Store(self.base("open")))["w"]["evidence"],
                            run(Store(self.base("closed")))["w"]["evidence"])

    def test_no_snapshot_means_no_assignee_not_a_guess(self):
        s = Store(item("w", "blocked") + item("d", "open") + [("w", "blockedOn", "d")])
        self.assertIsNone(run(s)["w"]["assignee"])


class EventId(unittest.TestCase):
    """aegis-2qo001, sattler's ruling: at-least-once delivery, receiver dedupes on
    a DETERMINISTIC event id derived from (WorkItem, causing transition)."""

    def unblocked(self, close_obs="obs-close-1", closed_at="2026-09-23T00:00:00Z"):
        return Store(item("w", "blocked") + item("d", "closed", at=closed_at, obs=close_obs)
                     + [("w", "blockedOn", "d")])

    def test_replaying_the_same_transition_gives_the_same_id(self):
        first = run(self.unblocked())["w"]
        second = run(self.unblocked())["w"]
        self.assertEqual("UNBLOCKED", first["verdict"])
        self.assertEqual(first["event_id"], second["event_id"])

    def test_a_genuine_second_unblock_is_a_new_id(self):
        # the blocker was reopened and closed again: a new, content-addressed Observation
        first = run(self.unblocked("obs-close-1", "2026-09-23T00:00:00Z"))["w"]["event_id"]
        again = run(self.unblocked("obs-close-2", "2026-09-25T00:00:00Z"))["w"]["event_id"]
        self.assertNotEqual(first, again)

    def test_a_blocked_verdict_carries_no_event(self):
        s = Store(item("w", "blocked") + item("d", "open") + [("w", "blockedOn", "d")])
        self.assertNotIn("event_id", run(s)["w"])

    def deliver_with_a_crash(self, mint):
        """Send, crash before the checkpoint, re-run: what does the receiver hold?"""
        received = set()  # the idempotent receiver dedupes on the event id
        checkpoint = set()
        for attempt in range(2):
            verdict = run(self.unblocked())["w"]
            key = mint(verdict)
            if key in checkpoint:
                continue
            received.add(key)  # the send succeeds...
            if attempt == 0:
                continue  # ...and the process dies before it records the checkpoint
            checkpoint.add(key)
        return received

    def test_a_crash_between_send_and_checkpoint_delivers_exactly_one_event(self):
        self.assertEqual(1, len(self.deliver_with_a_crash(lambda v: v["event_id"])))

    def test_SABOTAGE_a_send_time_uuid_would_deliver_two(self):
        # The arm that proves the test above can fail: an id minted at send time
        # (what the ruling forbids) makes the receiver's dedupe vacuous.
        import uuid
        self.assertEqual(2, len(self.deliver_with_a_crash(lambda v: str(uuid.uuid4()))))


if __name__ == "__main__":
    unittest.main()


def q_item(name, status=None, at="2026-09-20T00:00:00Z", obs=None):
    """A WorkItem written entirely in quechua terms. `observes` has no quechua
    twin in the transition map, so it stays legacy (aegis-9dpcta)."""
    out = [(name, "a", "q:WorkItem")]
    if status:
        o = obs or f"obs-{name}-{at}"
        out += [(name, "observes", o), (o, "q:observedAt", at), (o, "q:observedStatus", status)]
    return out


class QuechuaDualRead(unittest.TestCase):
    """aegis-9dpcta: the served quipu does not honour owl:equivalentClass, so
    the reader names both IRIs. Old, new and mixed data read alike."""

    def test_a_quechua_typed_graph_blocks_and_unblocks_like_a_legacy_one(self):
        for maker, edge in ((item, "blockedOn"), (q_item, "q:blockedOn")):
            with self.subTest(edge=edge):
                store = Store(maker("work", "open") + maker("dep", "open") + [("work", edge, "dep")])
                self.assertEqual(run(store)["work"]["verdict"], "BLOCKED")
                store.t.update(maker("dep", "closed", at="2026-09-25T00:00:00Z"))
                self.assertEqual(run(store)["work"]["verdict"], "UNBLOCKED")

    def test_mixed_edges_are_both_read(self):
        store = Store(item("work", "open") + item("a", "closed") + q_item("b", "open")
                      + [("work", "blockedOn", "a"), ("work", "q:blockedOn", "b")])
        r = run(store)["work"]
        self.assertEqual(sorted(b["target"] for b in r["blockers"]), ["a", "b"])
        self.assertEqual(r["verdict"], "BLOCKED")

    def test_the_same_edge_under_both_iris_is_one_blocker_with_unchanged_evidence(self):
        legacy = run(Store(item("work", "open") + item("dep", "open") + [("work", "blockedOn", "dep")]))
        dual = run(Store(item("work", "open") + item("dep", "open")
                         + [("work", "blockedOn", "dep"), ("work", "q:blockedOn", "dep")]))
        self.assertEqual(len(dual["work"]["blockers"]), 1)
        self.assertEqual(dual["work"]["evidence"], legacy["work"]["evidence"])

    def test_a_quechua_blocker_resolves_by_date(self):
        store = Store(item("work", "open") + [("b", "a", "q:Blocker"), ("b", "q:resolvesOn", "2026-09-01"),
                                              ("work", "q:blockedOn", "b")])
        self.assertEqual(run(store)["work"]["verdict"], "UNBLOCKED")

    def test_a_projected_quechua_dependency_blocks(self):
        store = Store(item("work", "open", obs="o1") + item("dep", "open")
                      + [("o1", "q:observedBlockedOn", "dep")])
        self.assertEqual(run(store, item="work")["work"]["verdict"], "BLOCKED")
        self.assertEqual(run(store)["work"]["verdict"], "BLOCKED")

    def test_controls_absent_and_foreign_namespace_edges_are_not_read(self):
        store = Store(item("work", "open") + item("dep", "open") + [("work", "x:blockedOn", "dep")])
        self.assertEqual(run(store), {})  # a third namespace is no blocker
        self.assertEqual(run(Store(item("work", "open"))), {})  # absent: nothing blocks


class CompactIriPrefixes(unittest.TestCase):
    def test_known_aliases_and_full_iris(self):
        for prefix in ("a:", "aegis:", bb.A):
            with self.subTest(prefix=prefix):
                self.assertEqual(bb._iri(prefix + "example"), bb.A + "example")
                self.assertEqual(bb.item_iri(prefix + "example"), bb.A + "example")

    def test_other_namespaces_are_not_rewritten(self):
        self.assertEqual(bb._iri("schema:name"), bb.SCHEMA + "name")
        self.assertEqual(bb._iri("unknown:example"), "unknown:example")
        self.assertEqual(bb._iri("https://example.test/item"), "https://example.test/item")
