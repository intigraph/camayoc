"""directive_edges (aegis-q9m5mp.47) against a graph-aware fake of quipu's
/query and /knot, and a fake Jev transport. No network."""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import blocked_by as bb
import directive_edges as de
import jev

A = bb.A
PROV = de.PROV
NOW = dt.datetime(2026, 10, 7, 22, 0, tzinfo=dt.timezone.utc)
POLICY = A + "policy_measurement-needs-a-control"
OTHER = A + "policy_load-is-bounded"
TRACKER = A + "aegis-q9m5mp.10"


class FakeQuipu:
    """Triples per graph (None = ROOT). Answers single bound patterns, the
    FILTER(?t = <X>) type form, and /knot into a named graph."""

    def __init__(self):
        self.g = {None: set(), de.DECLARED: set(), de.INFERRED: set()}
        self.knots = []
        self.drop_writes = False

    def add(self, s, p, o, graph=None):
        self.g[graph].add((s, p, o))

    def __call__(self, endpoint, body):
        if endpoint == "/knot":
            self.knots.append(body)
            if not self.drop_writes:
                for line in body["turtle"].strip().splitlines():
                    s, p, o = re.match(r"<([^>]+)> <([^>]+)> (.+) \.$", line).groups()
                    self.add(s, p, o[1:-1] if o.startswith("<") else o.split("^^")[0].strip('"'),
                             body["graph"])
            return {"tx_id": 1, "conforms": True}
        q, graph = body["query"], body.get("graph")
        triples = self.g.get(graph, set())
        m = re.search(r"(\?\w+|<[^>]+>) a \?t \. FILTER\(\?t = <([^>]+)>\)", q)
        if m:
            subj, cls = m.groups()
            if subj.startswith("?"):
                return {"rows": [{subj[1:]: s} for s, p, o in triples if p == "a" and o == cls]}
            return {"rows": [{"t": o} for s, p, o in triples
                             if s == subj.strip("<>") and p == "a" and o == cls]}
        m = re.search(r"\{ (\S+) <([^>]+)> (\S+) \}", q)
        s, p, o = m.groups()
        rows = []
        for ts, tp, to in triples:
            if tp != p or (not s.startswith("?") and s.strip("<>") != ts):
                continue
            row = {}
            if s.startswith("?"):
                row[s[1:]] = ts
            if o.startswith("?"):
                row[o[1:]] = to
            rows.append(row)
        return {"rows": rows}


def jev_answering(choice, confidence):
    calls = []

    def transport(body, key):
        calls.append(body)
        return {"answers": {"q": {"choice": choice, "confidence": confidence, "probabilities": {}}}}
    return jev.JevClient(api_key="", transport=transport), calls


def board():
    q = FakeQuipu()
    q.add(POLICY, A + "claim", "A claim is evidence only after a positive control.")
    q.add(OTHER, A + "claim", "Work bounds the load it puts on shared services.")
    q.add(POLICY, "a", A + "Policy")
    q.add(OTHER, "a", A + "Policy")
    # aegis:claim is NOT Policy-specific (615 Artifacts carry it live): an
    # Artifact's claim must never become a Jev option.
    q.add(A + "artifact-x", A + "claim", "A paper claims something.")
    q.add(A + "artifact-x", "a", A + "Artifact")
    # a traced member of POLICY, in the declared plane: the tracker comes from it
    q.add(A + "old", "a", A + "Directive")
    q.add(A + "old", A + "governedBy", POLICY, de.DECLARED)
    q.add(A + "old", A + "trackedBy", TRACKER, de.DECLARED)
    # the new, untraced Directive and its capture episode
    q.add(A + "new", "a", A + "Directive")
    q.add(A + "new", "http://www.w3.org/2000/01/rdf-schema#label", "zero needs a control")
    q.add(A + "new", PROV + "wasGeneratedBy", A + "episode_new")
    q.add(A + "episode_new", PROV + "generatedAtTime", "2026-10-07T21:00:00Z")
    return q


class Judge(unittest.TestCase):
    def test_three_verdicts_by_plane(self):
        q = board()
        self.assertEqual(de.judge(q, "old", NOW)["verdict"], de.TRACED)
        self.assertEqual(de.judge(q, "new", NOW)["verdict"], de.UNTRACED)
        q.add(A + "new", A + "governedBy", POLICY, de.INFERRED)
        q.add(A + "new", A + "trackedBy", TRACKER, de.INFERRED)
        self.assertEqual(de.judge(q, "new", NOW)["verdict"], de.PROPOSED,
                         "inferred-only edges are a proposal, not traced")

    def test_one_edge_is_not_traced(self):
        q = board()
        q.add(A + "new", A + "governedBy", POLICY, de.DECLARED)
        self.assertEqual(de.judge(q, "new", NOW)["verdict"], de.UNTRACED)

    def test_untraced_lapses_after_grace_from_capture(self):
        q = board()
        inside = de.judge(q, "new", NOW)
        self.assertEqual((inside["lapse"], inside["due_at"]), (de.NOT_LAPSED, "2026-10-09T21:00:00+00:00"))
        past = de.judge(q, "new", NOW + de.GRACE)
        self.assertEqual(past["lapse"], de.LAPSED)
        self.assertTrue(past["lapse_event_id"])

    def test_proposed_lapses_from_inferredAt_not_capture(self):
        q = board()
        q.add(A + "new", A + "governedBy", POLICY, de.INFERRED)
        q.add(A + "new", A + "trackedBy", TRACKER, de.INFERRED)
        q.add(A + "new", A + "inferredAt", "2026-10-08T21:00:00Z", de.INFERRED)
        self.assertEqual(de.judge(q, "new", NOW + de.GRACE)["lapse"], de.NOT_LAPSED)
        self.assertEqual(de.judge(q, "new", NOW + de.GRACE + dt.timedelta(days=1))["lapse"], de.LAPSED)

    def test_unanchored_untraced_reaches_a_person_now(self):
        # wu [wu-cm84-review] 1: 407/868 Directives have no datable episode;
        # one of those left UNTRACED must not wait forever.
        q = board()
        q.g[None] = {t for t in q.g[None] if t[1] != PROV + "wasGeneratedBy"}
        r = de.judge(q, "new", NOW)
        self.assertEqual((r["lapse"], r["due_at"]), (de.LAPSED, None))
        self.assertTrue(r["lapse_event_id"])

    def test_unanchored_traced_never_lapses(self):
        q = board()
        q.add(A + "old", "a", A + "Directive")
        self.assertEqual(de.judge(q, "old", NOW)["lapse"], de.NOT_LAPSED)

    def test_discover_is_asserted_directives_only(self):
        self.assertEqual(de.discover(board()), ["new", "old"])


class Policies(unittest.TestCase):
    def test_only_directly_typed_policies_are_options(self):
        self.assertEqual(set(de.policies(board())), {POLICY, OTHER})

    def test_a_promoted_policy_with_its_own_tracker_is_an_option_from_day_one(self):
        # q9m5mp.47: a NEW policy lives in crew/declared after promotion and has
        # no traced members yet; it declares its tracker on itself.
        q = board()
        new = A + "policy_design-prefers-queryable-sources"
        tracker = A + "aegis-q9m5mp.48"
        q.add(new, "a", A + "Policy", de.DECLARED)
        q.add(new, A + "claim", "Prefer a queryable entity pointing at its source.", de.DECLARED)
        q.add(new, A + "trackedBy", tracker, de.DECLARED)
        self.assertIn(new, de.policies(q))
        self.assertEqual(de.trackers(q)[new], tracker)
        client, _ = jev_answering("policy_design-prefers-queryable-sources", 0.9)
        out = de.propose(q, q, client, {"item": "new", "event_id": "e"}, NOW)
        self.assertEqual((out["outcome"], out["policy"], out["tracker"]), ("proposed", new, tracker))

    def test_a_declared_policy_tracker_beats_member_votes(self):
        q = board()
        q.add(POLICY, A + "trackedBy", A + "aegis-declared", de.DECLARED)
        self.assertEqual(de.trackers(q)[POLICY], A + "aegis-declared")

    def test_too_many_options_refuse_before_sending(self):
        q = board()
        for i in range(300):
            q.add(A + f"p{i}", A + "claim", "c")
            q.add(A + f"p{i}", "a", A + "Policy")
        client, calls = jev_answering("policy_measurement-needs-a-control", 0.9)
        with self.assertRaises(ValueError):
            de.plan(q, client, "new", NOW)
        self.assertEqual(calls, [], "nothing sent")


class Propose(unittest.TestCase):
    def event(self):
        return {"item": "new", "event_id": de.event_id("new")}

    def test_confident_pick_is_written_to_inferred_with_the_policys_tracker(self):
        q = board()
        client, calls = jev_answering("policy_measurement-needs-a-control", 0.91)
        out = de.propose(q, q, client, self.event(), NOW)
        self.assertEqual((out["outcome"], out["verdict"]), ("proposed", de.PROPOSED))
        (knot,) = q.knots
        self.assertEqual(knot["graph"], de.INFERRED)
        self.assertIn(f"<{POLICY}>", knot["turtle"])
        self.assertIn(f"<{TRACKER}>", knot["turtle"])
        self.assertNotIn((A + "new", A + "governedBy", POLICY), q.g[de.DECLARED],
                         "ingress never writes the declared plane")
        self.assertIn("none-of-these", calls[0]["questions"]["q"]["criteria"])

    def test_redelivery_is_a_noop(self):
        q = board()
        client, calls = jev_answering("policy_measurement-needs-a-control", 0.91)
        de.propose(q, q, client, self.event(), NOW)
        again = de.propose(q, q, client, self.event(), NOW)
        self.assertEqual((again["outcome"], len(q.knots), len(calls)), ("noop", 1, 1))

    def test_below_floor_none_or_trackerless_write_nothing(self):
        for choice, conf, why in (("policy_measurement-needs-a-control", 0.74, "confidence"),
                                  ("none-of-these", 0.99, "none"),
                                  ("policy_load-is-bounded", 0.95, "no tracker")):
            q = board()
            client, _ = jev_answering(choice, conf)
            out = de.propose(q, q, client, self.event(), NOW)
            self.assertEqual((out["outcome"], q.knots), ("needs-ruling", []), choice)
            self.assertIn(why, out["why"])

    def test_a_write_that_does_not_read_back_raises_for_retry(self):
        q = board()
        q.drop_writes = True
        client, _ = jev_answering("policy_measurement-needs-a-control", 0.91)
        with self.assertRaises(ConnectionError):
            de.propose(q, q, client, self.event(), NOW)

    def test_a_non_directive_event_is_a_noop_and_never_calls_jev(self):
        q = board()
        client, calls = jev_answering("policy_measurement-needs-a-control", 0.95)
        out = de.propose(q, q, client, {"item": "policy_measurement-needs-a-control", "event_id": "x"}, NOW)
        self.assertEqual((out["outcome"], out["verdict"], calls), ("noop", "NOT_A_DIRECTIVE", []))
        self.assertEqual(q.knots, [])

    def test_traced_directive_never_calls_jev(self):
        q = board()
        client, calls = jev_answering("policy_measurement-needs-a-control", 0.91)
        out = de.propose(q, q, client, {"item": "old", "event_id": "x"}, NOW)
        self.assertEqual((out["outcome"], calls), ("noop", []))


if __name__ == "__main__":
    unittest.main()


class AdapterProtocol(unittest.TestCase):
    """The two views as chaski's change-driven emitter sees them."""

    def setUp(self):
        import change_adapter as ca
        self.ca = ca
        self.now = NOW.timestamp()

    def test_subscription_covers_three_planes_and_the_directive_type(self):
        d = self.ca.description("directive")
        self.assertEqual(d["types"], [A + "Directive"])
        self.assertEqual(set(d["graphs"]), {"ROOT", de.DECLARED, de.INFERRED})
        self.assertIn(A + "governedBy", d["attributes"])

    def test_a_new_directive_routes_to_itself(self):
        out = self.ca.evaluate("directive", board(), {"now": self.now, "route": [
            {"entity": A + "new", "attribute": "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"}]})
        self.assertEqual(out["items"], ["new"])

    def test_a_routed_policy_is_not_judged(self):
        # 2026-10-08: a new Policy's trackedBy change routed it here, it was
        # judged UNTRACED and paged DirectiveUntraced. Only Directives are judged.
        q = board()
        pol = "policy_design-prefers-queryable-sources"
        q.add(A + pol, "a", A + "Policy", de.INFERRED)
        q.add(A + pol, A + "trackedBy", A + "aegis-q9m5mp.48", de.INFERRED)
        for kind in ("directive", "directive-lapse"):
            out = self.ca.evaluate(kind, q, {"now": self.now, "items": [pol]})
            self.assertEqual(out["records"], [], kind)

    def test_untraced_view_carries_the_untraced_event(self):
        out = self.ca.evaluate("directive", board(), {"now": self.now, "items": ["new", "old"]})
        verdicts = {r["entity"]: (r["verdict"], r["event_id"]) for r in out["records"]}
        self.assertEqual(verdicts["new"], (de.UNTRACED, de.event_id("new")))
        self.assertEqual(verdicts["old"], (de.TRACED, None))

    def test_untraced_view_reports_a_proposal_as_untraced(self):
        # aegis-df7h: chaski admits only TRACED/UNTRACED/UNKNOWN for this view.
        q = board()
        q.add(A + "new", A + "governedBy", POLICY, de.INFERRED)
        q.add(A + "new", A + "trackedBy", TRACKER, de.INFERRED)
        self.assertEqual(de.judge(q, "new", NOW)["verdict"], de.PROPOSED)
        out = self.ca.evaluate("directive", q, {"now": self.now, "items": ["new", "old"]})
        verdicts = {r["entity"]: r["verdict"] for r in out["records"]}
        self.assertEqual(verdicts, {"new": de.UNTRACED, "old": de.TRACED})
        # wu review: chaski refuses UNTRACED without an event_id; it must equal the
        # identity a plain UNTRACED record carries, so emitter state stays keyed alike.
        events = {r["entity"]: r["event_id"] for r in out["records"]}
        self.assertEqual(events["new"], de.event_id("new"))
        self.assertIsNone(events["old"])
        for r in out["records"]:
            if r["verdict"] == de.UNTRACED:
                self.assertTrue(r["event_id"], "UNTRACED without event_id")
        self.assertTrue(set(verdicts.values()) <= {de.TRACED, de.UNTRACED, "UNKNOWN"})

    def test_lapse_view_stores_the_grace_deadline_then_lapses(self):
        out = self.ca.evaluate("directive-lapse", board(), {"now": self.now, "items": ["new"]})
        (r,) = out["records"]
        self.assertEqual(r["verdict"], de.NOT_LAPSED)
        due = dt.datetime(2026, 10, 9, 21, 0, tzinfo=dt.timezone.utc).timestamp()
        self.assertEqual(out["next_checks"]["new"], due)
        later = self.ca.evaluate("directive-lapse", board(), {"now": due + 1, "items": ["new"]})
        (r,) = later["records"]
        self.assertEqual(r["verdict"], de.LAPSED)
        self.assertTrue(r["event_id"])
        self.assertIsNone(later["next_checks"]["new"])


class Cli(unittest.TestCase):
    def test_chaski_invocation_shape(self):
        """chaski appends --describe / --changes to the configured command."""
        import subprocess
        script = Path(__file__).resolve().parents[1] / "scripts" / "directive_edges.py"
        for view, kind in (("adapter", "directive"), ("lapse-adapter", "directive-lapse")):
            out = subprocess.run([sys.executable, str(script), view, "--describe"],
                                 capture_output=True, text=True, check=False, timeout=30)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(json.loads(out.stdout)["types"], [A + "Directive"], kind)

    def test_propose_reads_credentials_from_files(self):
        import io
        import os
        import tempfile
        from unittest import mock

        planes = de.planes  # the module object directive_edges writes to
        d = Path(tempfile.mkdtemp())
        (d / "tok").write_text("bearer-from-file\n")
        (d / "jev").write_text("k" * 24)
        seen = {}

        def fake_propose(post, write, client, event):
            seen["auth"], seen["keyfile"] = planes.AUTH, os.environ.get(jev.KEY_FILE_ENV)
            return {"outcome": "noop"}

        with mock.patch.object(de, "propose", fake_propose), \
             mock.patch.object(jev, "JevClient", lambda: object()), \
             mock.patch.object(planes, "AUTH", None), \
             mock.patch.dict(os.environ, {}, clear=False), \
             mock.patch("sys.stdin", io.StringIO('{"item": "new", "event_id": "x"}')), \
             mock.patch("sys.stdout", io.StringIO()):
            de.main(["propose", "--token-file", str(d / "tok"), "--jev-key-file", str(d / "jev")])
        self.assertEqual(seen, {"auth": "bearer-from-file", "keyfile": str(d / "jev")})
