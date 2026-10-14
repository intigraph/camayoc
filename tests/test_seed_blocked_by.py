"""Native seeds board verdicts and their incremental change path (aegis-edptky)."""
import os
import unittest
from unittest.mock import patch

from test_blocked_by import bb, run
from sparql_fake import TripleStore
import change_adapter as ca

BOARD = "https://seeds.local/project/aegis"
W = bb.SEED_ITEM + "sd-a%2Fb"
D = bb.SEED_ITEM + "sd-dependency"


class NativeTerms(TripleStore):
    def _term(self, token):
        if token.startswith("<https://seeds.local/") or token.startswith("<https://schema.org/"):
            return "iri", token[1:-1]
        return super()._term(token)


class BoardStore:
    def __init__(self, status="open", own_status="open", graph=BOARD):
        self.graph = graph
        self.triples = {(W, "a", bb.SCHEMA + "Action"), (D, "a", bb.SCHEMA + "Action"),
                        (W, "q:blockedOn", D), (W, bb.SEED_STATUS, own_status),
                        (D, bb.SCHEMA + "dateModified", "2026-10-08T00:00:00Z"),
                        (W, bb.SCHEMA + "agent", "https://seeds.local/principal/ian")}
        if status is not None:
            self.triples.add((D, bb.SEED_STATUS, status))

    def post(self, endpoint, body):
        rows = NativeTerms(self.triples, bb.A, bb.Q, {"blockedOn", bb.SCHEMA + "agent"}).select(body["query"]) if body.get("graph") == self.graph else []
        return {"rows": rows, "truncated": False}


class SeedBlockers(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"CAMAYOC_BOARD_GRAPHS": BOARD})
        env.start()
        self.addCleanup(env.stop)

    def test_open_closed_and_missing_status(self):
        for status, expected in (("open", "BLOCKED"), ("deferred", "BLOCKED"),
                                 ("closed", "UNBLOCKED"), ("tombstone", "UNBLOCKED"),
                                 (None, "UNKNOWN")):
            with self.subTest(status=status):
                record = run(BoardStore(status), item=W)[W]
                self.assertEqual(record["verdict"], expected)
                self.assertEqual(record["assignee"], "ian")
                self.assertEqual(record["blockers"][0]["target"], D)

    def test_population_and_bound_item_agree_and_closed_item_is_omitted(self):
        store = BoardStore()
        self.assertEqual(run(store), run(store, item=W))
        self.assertEqual(run(BoardStore(own_status="closed")), {})

    def test_compact_schema_type_matches_full_iri_verdicts(self):
        # Quipu's served query rows compact the native seed type to schema:Action.
        for status in ("open", "deferred", "closed", "tombstone", None):
            with self.subTest(status=status):
                store = BoardStore(status)
                def post(endpoint, body):
                    result = store.post(endpoint, body)
                    result["rows"] = [
                        {key: "schema:Action" if value == bb.SCHEMA + "Action" else value
                         for key, value in row.items()} for row in result["rows"]]
                    return result
                self.assertEqual(bb.evaluate(post, item=W), bb.evaluate(store.post, item=W))

    def test_reopen_overrides_old_close_metadata_and_changes_event_identity(self):
        store = BoardStore("closed")
        first = run(store)[W]["event_id"]
        store.triples.add((D, bb.SCHEMA + "endTime", "2026-10-07T00:00:00Z"))
        store.triples.remove((D, bb.SEED_STATUS, "closed"))
        store.triples.add((D, bb.SEED_STATUS, "open"))
        self.assertEqual(run(store)[W]["verdict"], "BLOCKED")
        store.triples.remove((D, bb.SEED_STATUS, "open"))
        store.triples.add((D, bb.SEED_STATUS, "closed"))
        store.triples.remove((D, bb.SCHEMA + "dateModified", "2026-10-08T00:00:00Z"))
        store.triples.add((D, bb.SCHEMA + "dateModified", "2026-10-08T01:00:00Z"))
        self.assertNotEqual(run(store)[W]["event_id"], first)

    def test_pre_change_graph_mutant_misses_seed(self):
        original = bb.graphs()
        with patch.object(bb, "graphs", return_value=[g for g in original if g != BOARD]):
            self.assertEqual(run(BoardStore(), item=W), {})
        self.assertEqual(run(BoardStore(), item=W)[W]["verdict"], "BLOCKED")

    def test_unproven_board_read_refuses_instead_of_reporting_no_blockers(self):
        store = BoardStore()
        def post(endpoint, body):
            return {"rows": [], "truncated": True} if body.get("graph") == BOARD else store.post(endpoint, body)
        with self.assertRaises(ValueError):
            bb.evaluate(post, item=W)
        with self.assertRaises(ValueError):
            ca.local(W + "> ?s ?p ?o")

    def test_custom_boards_and_inferred_refusal(self):
        with patch.dict(os.environ, {"CAMAYOC_BOARD_GRAPHS": "urn:board:custom,urn:board:custom"}):
            self.assertEqual(bb.graphs().count("urn:board:custom"), 1)
            self.assertEqual(run(BoardStore(graph="urn:board:custom"))[W]["verdict"], "BLOCKED")
        with patch.dict(os.environ, {"CAMAYOC_BOARD_GRAPHS": bb.planes.plane_for("inferred")}):
            with self.assertRaises(ValueError):
                bb.graphs()
        self.assertEqual(run(BoardStore(graph=bb.planes.plane_for("inferred"))), {})

    def test_status_change_routes_dependents_and_matches_full_evaluation(self):
        store = BoardStore("closed")
        change = {"entity": D, "attribute": bb.SEED_STATUS, "graph": BOARD}
        result = ca.evaluate("blocked", store.post, {"now": 1791417600, "changes": [change]})
        self.assertIn(W, result["scope"])
        self.assertEqual(result["records"], list(run(store).values()))
        discovery = ca.evaluate("blocked", store.post, {"now": 1791417600, "discover": True})
        self.assertEqual(discovery["items"], [W])
        desc = ca.description("blocked")
        self.assertIn(BOARD, desc["graphs"])
        self.assertIn(bb.SEED_STATUS, desc["attributes"])
        self.assertIn(bb.SCHEMA + "Action", desc["types"])
        self.assertIsNone(ca.local(W + "/comment/1"))


if __name__ == "__main__":
    unittest.main()
