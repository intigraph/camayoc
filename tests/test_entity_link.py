"""Entity linking: search retrieves, Jev picks (aegis-4hhqoe.12).

Stubbed search and Jev: these prove what the linker does with what it is
given — what it offers, what it may return, and that a failed call is an
error rather than an abstention. The live evaluation is on the bead.
"""
from __future__ import annotations

import sys
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import entity_link as el  # noqa: E402
import jev  # noqa: E402


def search_of(by_query: dict[str, list[str]]):
    def search(q):
        key = "title" if "\n" not in q else "full"
        return {"results": [{"entity": e, "text": f"text of {e}"} for e in by_query[key]]}
    return search


class StubClient:
    def __init__(self, picks):
        self.picks = list(picks)
        self.offered = []

    def choice(self, state, instructions, criteria, none_text=None):
        self.offered.append(dict(criteria))
        pick = self.picks.pop(0)
        if isinstance(pick, BaseException):
            raise pick
        return {"choice": pick, "confidence": 0.9, "usage": {}}


SEARCH = search_of({
    "title": ["aegis:svc-a", "aegis:episode_ingest-x", "aegis:bead-closed-aegis-1"],
    "full": ["aegis:svc-b", "aegis:svc-a",
             "http://aegis.gastown.local/ontology/commit/repo/abc",
             "http://aegis.gastown.local/ontology/code/repo/f.rs::sym",
             "aegis:ledger-0123456789ab-a-directive-row"],
})


class EntityLinkTest(unittest.TestCase):
    def test_actual_request_keeps_auth_body_and_shared_provenance(self):
        import io
        import json
        seen = {}

        class Response(io.BytesIO):
            pass

        def fake(req, timeout=None):
            seen.update(headers={k.lower(): v for k, v in req.header_items()},
                        body=json.loads(req.data), timeout=timeout)
            return Response(b'{"conforms":true}')

        env = {"CODEX_HOME": "/tmp/codex", "SHANTY_AGENT": "reviewer",
               "CODEX_SESSION_ID": "session", "QUIPU_HOST": "test-host"}
        with mock.patch.dict("os.environ", env, clear=True), \
                mock.patch("urllib.request.urlopen", fake):
            self.assertTrue(el._post("/knot", {"turtle": "unchanged"}, "token")["conforms"])
        headers = seen["headers"]
        self.assertEqual(headers["x-quipu-client"], "camayoc-entity-link")
        self.assertEqual(headers["authorization"], "Bearer token")
        self.assertEqual([headers[f"x-quipu-{k}"] for k in ("agent", "harness", "session", "host")],
                         ["reviewer", "codex", "session", "test-host"])
        self.assertNotIn("x-quipu-model", headers)
        self.assertEqual((seen["body"], seen["timeout"]), ({"turtle": "unchanged"}, 60))

    def test_provenance_lifecycle_and_passages_are_never_offered(self):
        offered = [c["entity"] for c in el.candidates("t", "d", SEARCH)]
        self.assertEqual(offered, ["aegis:svc-a", "aegis:svc-b"])

    def test_the_item_itself_is_never_a_candidate(self):
        search = search_of({"title": ["aegis:aegis-x1", "aegis:svc-a"],
                            "full": ["http://aegis.gastown.local/ontology/workitem/aegis-x1",
                                     "aegis:aegis-x2"]})
        offered = [c["entity"] for c in el.candidates("t", "d", search, item="aegis-x1")]
        self.assertEqual(offered, ["aegis:svc-a", "aegis:aegis-x2"])

    def test_title_and_full_results_interleave_without_duplicates(self):
        search = search_of({"title": ["aegis:t1", "aegis:t2"],
                            "full": ["aegis:f1", "aegis:t1", "aegis:f2"]})
        offered = [c["entity"] for c in el.candidates("t", "d", search)]
        self.assertEqual(offered, ["aegis:t1", "aegis:f1", "aegis:t2", "aegis:f2"])

    def test_the_pool_is_capped(self):
        many = [f"aegis:e{i}" for i in range(50)]
        search = search_of({"title": many, "full": many})
        self.assertEqual(len(el.candidates("t", "d", search)), el.TOP_K)

    def test_an_offered_pick_is_returned(self):
        client = StubClient(["aegis:svc-b"])
        res = el.link("t", "d", client, SEARCH)
        self.assertEqual(res["choice"], "aegis:svc-b")
        self.assertIsNone(res["error"])
        self.assertEqual(set(client.offered[0]), {"aegis:svc-a", "aegis:svc-b"})

    def test_none_of_these_is_an_abstention_not_an_error(self):
        res = el.link("t", "d", StubClient([jev.NONE_OPTION]), SEARCH)
        self.assertIsNone(res["choice"])
        self.assertIsNone(res["error"])

    def test_an_unoffered_answer_is_never_returned(self):
        res = el.link("t", "d", StubClient(["aegis:never-offered"]), SEARCH)
        self.assertIsNone(res["choice"])

    def test_one_timeout_is_retried(self):
        res = el.link("t", "d", StubClient([TimeoutError("read timed out"), "aegis:svc-a"]), SEARCH)
        self.assertEqual(res["choice"], "aegis:svc-a")
        self.assertIsNone(res["error"])

    def test_a_persistent_failure_is_an_error_not_an_abstention(self):
        client = StubClient([TimeoutError("t1"), TimeoutError("t2")])
        res = el.link("t", "d", client, SEARCH)
        self.assertIsNone(res["choice"])
        self.assertIn("TimeoutError", res["error"])
        self.assertEqual(len(client.offered), el.RETRIES + 1)

    def test_no_candidates_asks_nothing(self):
        client = StubClient([])
        res = el.link("t", "d", client, search_of({"title": [], "full": []}))
        self.assertIsNone(res["choice"])
        self.assertEqual(client.offered, [])

    def _write(self, responses):
        """Run write_link against scripted /knot answers; return (posts, ensured)."""
        import io
        import urllib.error
        posts, ensured = [], []
        fake = type(sys)("planes")
        fake.ensure_planes = lambda ts: ensured.append(ts)
        fake.plane_for = lambda kind: f"urn:plane:{kind}"
        answers = list(responses)

        def post(path, body, token=""):
            posts.append((path, body))
            a = answers.pop(0)
            if isinstance(a, str):
                raise urllib.error.HTTPError(path, 400, "bad", {}, io.BytesIO(a.encode()))
            return a
        with mock.patch.dict(sys.modules, {"planes": fake}), \
                mock.patch.object(el, "_post", post):
            el.write_link("aegis-x1", "aegis:svc-a", "tok", "2026-10-01T00:00:00Z")
        return posts, ensured

    def test_the_written_link_is_inferred_and_quarantined(self):
        posts, ensured = self._write([{"conforms": True}])
        path, body = posts[0]
        self.assertEqual(path, "/knot")
        self.assertEqual(body["graph"], "urn:plane:inferred")
        self.assertIn(f"<{el.ONTOLOGY}about> <{el.ONTOLOGY}svc-a>", body["turtle"])
        self.assertIn('"inferred"', body["turtle"])
        self.assertEqual(ensured, [], "an existing plane is not re-registered")

    def test_an_unregistered_plane_is_registered_once_then_written(self):
        posts, ensured = self._write(
            ["unknown graph: urn:plane:inferred — create and register it first",
             {"conforms": True}])
        self.assertEqual(len(ensured), 1)
        self.assertEqual([p for p, _ in posts], ["/knot", "/knot"])

    def test_any_other_refusal_is_raised_not_retried(self):
        with self.assertRaises(Exception):
            self._write(["shape violation"])



class AuthoritativeInputTest(unittest.TestCase):
    def payload(self):
        return {'version': 1, 'items': [{'id': 'aegis-seeds-only.12',
                'title': 'literal $(do-not-run) `do-not-run`',
                'description': 'quoted "text"\nnext line'}]}

    def test_stdin_preserves_text_and_never_resolves_an_id_with_br(self):
        import io, json, contextlib
        payload = self.payload()
        calls = []
        def link(title, description, client, item=''):
            calls.append((item, title, description))
            return {'choice': None, 'error': None}
        out = io.StringIO()
        with mock.patch.object(sys, 'stdin', io.StringIO(json.dumps(payload))), \
                mock.patch.object(el, 'bead', side_effect=AssertionError('br lookup')), \
                mock.patch.object(el.jev, 'JevClient', return_value=object()), \
                mock.patch.object(el, 'link', side_effect=link), contextlib.redirect_stdout(out):
            self.assertEqual(el.main(['--work-item-json', '-']), 0)
        row = payload['items'][0]
        self.assertEqual(calls, [(row['id'], row['title'], row['description'])])
        self.assertEqual(json.loads(out.getvalue())['item'], row['id'])

    def test_invalid_input_is_refused_before_any_client_or_partial_write(self):
        import io, json, contextlib
        cases = [[], {'version': True, 'items': []}, {'version': 2, 'items': []},
                 {'version': 1, 'items': []}, self.payload()]
        cases[-1]['items'].append({'id': 'invalid ID', 'title': 'other', 'description': ''})
        for payload in cases:
            with self.subTest(payload=payload), \
                    mock.patch.object(sys, 'stdin', io.StringIO(json.dumps(payload))), \
                    mock.patch.object(el.jev, 'JevClient') as client, \
                    contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as exc:
                    el.main(['--work-item-json', '-'])
                self.assertEqual(exc.exception.code, 2)
                client.assert_not_called()

    def test_mixing_authoritative_input_and_legacy_lookup_is_refused(self):
        import io, contextlib
        for args in [['aegis-old'], ['--db', 'other'], ['--db=other']]:
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), \
                    mock.patch.object(el.jev, 'JevClient') as client:
                with self.assertRaises(SystemExit):
                    el.main(['--work-item-json', '-', *args])
                client.assert_not_called()

    def test_legacy_explicit_database_still_supplies_the_same_text(self):
        import io, contextlib
        with mock.patch.object(el, 'bead', return_value=('title', 'description')) as bead, \
                mock.patch.object(el.jev, 'JevClient', return_value=object()), \
                mock.patch.object(el, 'link', return_value={'choice': None}) as link, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(el.main(['aegis-old', '--db', 'selected-db']), 0)
        bead.assert_called_once_with('aegis-old', 'selected-db')
        self.assertEqual(link.call_args.args[:2], ('title', 'description'))


if __name__ == "__main__":
    unittest.main()
