"""Review ages and deadline changes preserve arbitrary web entity identities."""
import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import blocked_by as bb
import change_adapter as ca
import review_due as rd
from sparql_fake import TripleStore

NOW = dt.datetime(2026, 10, 20, tzinfo=dt.timezone.utc)
ENTITY = "https://example.test/decisions/review"
VERIFICATION = "https://example.test/verification/first"


class WebTriples(TripleStore):
    """Retain the existing single-pattern fake, accepting external subjects."""
    def _term(self, token):
        if token.startswith(("<https://", "<http://")) and not token.startswith(("<" + self.a, "<" + self.q)):
            return "iri", token[1:-1]
        return super()._term(token)


class Store:
    def __init__(self, triples):
        self.triples = list(triples)

    def post(self, endpoint, body):
        if body.get("graph") != "https://camayoc.local/plane/crew/declared":
            return {"rows": [], "truncated": False}
        rows = WebTriples(self.triples, bb.A, bb.Q, {"ownedBy", "verifies", "observes"}).select(body["query"])
        return {"rows": rows, "truncated": False}


def changed(entity, attribute, **extra):
    return {"entity": entity, "attribute": bb.A + attribute,
            "graph": "https://camayoc.local/plane/crew/declared", **extra}


class ReviewEntityIris(unittest.TestCase):
    def test_three_entity_kinds_keep_identity_owner_and_deadline(self):
        triples = []
        subjects = ["https://example.test/" + kind for kind in ("decision", "directive", "count-claim")]
        for subject in subjects:
            triples += [(subject, "reviewAfter", "2026-10-19T00:00:00Z"),
                        (subject, "ownedBy", "aegis:wu")]
        rows = rd.evaluate(Store(triples).post, now=NOW)
        self.assertEqual({r["entity"] for r in rows}, set(subjects))
        self.assertTrue(all(r["verdict"] == "DUE" and r["owner"] == "wu" for r in rows))
        self.assertEqual(len({r["event_id"] for r in rows}), 3)

    def test_matching_change_routes_external_entity_but_not_blocked_adapter(self):
        event = changed(ENTITY, "reviewAfter")
        self.assertEqual(ca.targets(None, "review", [event], []), [ENTITY])
        self.assertEqual(ca.targets(None, "blocked", [event], []), [])

    def test_deadline_crossing_needs_no_graph_change_and_replay_keeps_identity(self):
        store = Store([(ENTITY, "reviewAfter", "2026-10-20T00:00:10Z"),
                       (ENTITY, "ownedBy", "aegis:wu")])
        def read(at):
            return ca.evaluate("review", store.post, {"items": [ENTITY], "now": at})
        first = read(NOW.timestamp())
        self.assertEqual(first["records"][0]["verdict"], "NOT_DUE")
        self.assertEqual(first["next_checks"][ENTITY], NOW.timestamp() + 10)
        due = read(NOW.timestamp() + 10)
        self.assertEqual(due["records"][0]["verdict"], "DUE")
        self.assertEqual(due["records"][0]["event_id"], read(NOW.timestamp() + 11)["records"][0]["event_id"])

    def test_external_verification_resets_clock_and_later_expiry_gets_new_id(self):
        store = Store([(ENTITY, "maxAge", "PT1M"), (ENTITY, "ownedBy", "aegis:wu"),
                       (VERIFICATION, "verifies", ENTITY),
                       (VERIFICATION, "verifiedAt", "2026-10-19T00:00:00Z")])
        first = rd.judge(store.post, ENTITY, NOW)
        self.assertEqual(first["verdict"], "DUE")
        newer = VERIFICATION + "-new"
        store.triples += [(newer, "verifies", ENTITY), (newer, "verifiedAt", NOW.isoformat())]
        fresh = ca.evaluate("review", store.post, {"now": NOW.timestamp(),
                            "changes": [changed(newer, "verifiedAt")]})
        self.assertEqual(fresh["scope"], [ENTITY])
        self.assertEqual(fresh["records"][0]["verdict"], "NOT_DUE")
        self.assertNotIn("event_id", fresh["records"][0])
        later = rd.judge(store.post, ENTITY, NOW + dt.timedelta(seconds=60))
        self.assertEqual(later["verdict"], "DUE")
        self.assertNotEqual(first["event_id"], later["event_id"])

    def test_removed_verification_link_still_routes_former_external_entity(self):
        store = Store([(ENTITY, "maxAge", "PT1M")])
        result = ca.evaluate("review", store.post, {"now": NOW.timestamp(), "changes": [
            changed(VERIFICATION, "verifies", old_value={"ref": ENTITY})]})
        self.assertEqual(result["scope"], [ENTITY])
        self.assertEqual(result["records"][0]["verdict"], "UNKNOWN")
        self.assertIsNone(result["next_checks"][ENTITY])

    def test_external_activity_anchor_is_preserved(self):
        observation = "https://example.test/observations/latest"
        store = Store([(ENTITY, "idleLimit", "PT1M"), (ENTITY, "observes", observation),
                       (observation, "observedAt", NOW.isoformat()),
                       (observation, "observedStatus", "open")])
        self.assertEqual(rd.judge(store.post, ENTITY, NOW)["verdict"], "NOT_DUE")
        self.assertEqual(rd.judge(store.post, ENTITY, NOW + dt.timedelta(seconds=60))["verdict"], "DUE")

    def test_no_age_entity_never_fires_and_unanchored_age_stays_unknown(self):
        self.assertEqual(rd.evaluate(Store([(ENTITY, "ownedBy", "aegis:wu")]).post, now=NOW), [])
        row = rd.judge(Store([(ENTITY, "maxAge", "PT1M")]).post, ENTITY, NOW)
        self.assertEqual(row["verdict"], "UNKNOWN")
        self.assertNotIn("event_id", row)

    def test_truncated_authority_is_not_a_known_empty_population(self):
        def truncated(*_):
            return {"rows": [], "truncated": True}
        with self.assertRaises(ValueError):
            rd.evaluate(truncated, now=NOW)

    def test_unsafe_external_reference_is_refused_before_query(self):
        for bad in (ENTITY + '"', ENTITY + "\n", ENTITY + " } UNION { ?s ?p ?o"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                ca.targets(None, "review", [changed(bad, "reviewAfter")], [])

    def test_legacy_expiry_event_id_is_byte_identical_to_baseline(self):
        row = rd.judge(Store([("legacy-age-control", "reviewAfter", "2026-10-19T00:00:00Z"),
                              ("legacy-age-control", "ownedBy", "aegis:wu")]).post,
                       "legacy-age-control", NOW)
        self.assertEqual(row["entity"], "legacy-age-control")
        self.assertEqual(row["event_id"], "sha256:26135d7e76172c691cd94aaf56e9fea592d86158e21f5d3378d0302cac6c7f40")
