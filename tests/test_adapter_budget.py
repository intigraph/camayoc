"""Request cap, exact cost report and paged snapshot-pinned discovery (aegis-ssvr C1).

Requests are counted where they leave the process (planes._post), so these
tests drive the real choke point with urlopen replaced by an in-memory store,
and one end-to-end arm runs an adapter process against a loopback fake server.
"""
import contextlib
import datetime as dt
import http.server
import io
import json
import os
import re
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import blocked_by as bb  # noqa: E402
import change_adapter as ca  # noqa: E402
import planes  # noqa: E402
from test_blocked_by import Store, item  # noqa: E402
from test_directive_edges import FakeQuipu  # noqa: E402
from test_inferred_memory_review import Store as MemoryStore, memory  # noqa: E402

NOW = dt.datetime(2026, 11, 4, tzinfo=dt.timezone.utc).timestamp()
W = 4242
KEYSET = re.compile(r'^SELECT DISTINCT \?s WHERE \{ (\{ .* \})(?: FILTER\(STR\(\?s\) > "([^"]*)"\))? \} '
                    r'ORDER BY STR\(\?s\) LIMIT (\d+)$')


class Keyset:
    """Adds the keyset page shape to a single-pattern test store and records
    every request body, so a test can check the snapshot pin on each one."""

    def __init__(self, inner):
        self.inner = inner
        self.bodies = []

    def __call__(self, endpoint, body):
        self.bodies.append((endpoint, body))
        m = KEYSET.match(body.get("query", ""))
        if not m:
            return self.inner(endpoint, body)
        core, after, limit = m.groups()
        found = {}
        for branch in core[2:-2].split(" } UNION { "):
            plain = {k: v for k, v in body.items() if k != "query"}
            for row in self.inner(endpoint, {**plain, "query": f"SELECT ?s WHERE {{ {branch} }}"})["rows"]:
                found.setdefault(bb._iri(str(row["s"])), row["s"])
        keys = sorted(k for k in found if after is None or k > after)[:int(limit)]
        return {"rows": [{"s": found[k]} for k in keys], "truncated": False}


@contextlib.contextmanager
def wire(store):
    """urlopen answered by `store`; yields the list of request bodies sent."""
    sent = []

    def urlopen(req, timeout=None):
        body = json.loads(req.data)
        sent.append(body)
        endpoint = req.full_url[len(planes.SERVER):]
        return contextlib.closing(io.BytesIO(json.dumps(store(endpoint, body)).encode()))

    with mock.patch.object(planes.urllib.request, "urlopen", urlopen):
        yield sent


def post(endpoint, body):
    return planes._post(endpoint, body, client=bb.CLIENT)


def drain(kind, store, k, **extra):
    """Every page of one snapshot under a k-request budget."""
    pages, cursor = [], None
    with wire(store) as sent:
        while True:
            before = len(sent)
            out = ca.run(kind, {"discover": True, "now": NOW, "as_of_tx": W, "cursor": cursor,
                                "budget": {"max_requests": k}, **extra}, post)
            pages.append(out)
            assert out["cost"]["quipu_requests"] == len(sent) - before <= k
            if out.get("complete"):
                return pages, sent
            cursor = out["next_cursor"]
            assert len(pages) < 500, "paging does not terminate"


def blocked_store():
    triples = []
    for i in range(7):  # declared blockers
        triples += item(f"w{i}", "open") + [(f"w{i}", "blockedOn", "d")]
    # projected blockers: latest Observation bears one -> discovered once
    for i in range(5):
        triples += item(f"p{i}", "open", at="2026-09-20T00:00:00Z", obs=f"o-p{i}-old")
        triples += [(f"p{i}", "observes", f"o-p{i}-new"), (f"o-p{i}-new", "observedAt", "2026-09-21T00:00:00Z"),
                    (f"o-p{i}-new", "observedBlockedOn", "d"), (f"o-p{i}-old", "observedBlockedOn", "d")]
    # only a superseded Observation bore a blocker -> not a candidate
    triples += item("gone", "open", at="2026-09-21T00:00:00Z", obs="o-gone-new")
    triples += [("gone", "observes", "o-gone-old"), ("o-gone-old", "observedAt", "2026-09-19T00:00:00Z"),
                ("o-gone-old", "observedBlockedOn", "d")]
    # both a declared and a projected blocker -> returned once, by phase 0
    triples += [("w0", "observes", "o-w0-b"), ("o-w0-b", "observedAt", "2026-09-30T00:00:00Z"),
                ("o-w0-b", "observedBlockedOn", "d")]
    return Store(triples)


class Cap(unittest.TestCase):
    def tearDown(self):
        planes.start_request_budget(None)

    def test_request_k_plus_one_is_refused_before_it_is_sent(self):
        planes.start_request_budget(3)
        with wire(lambda e, b: {"rows": []}) as sent:
            for _ in range(3):
                post("/query", {"query": "SELECT ?s WHERE { ?s ?p ?o }"})
            with self.assertRaises(planes.RequestBudgetExceeded):
                post("/query", {"query": "SELECT ?s WHERE { ?s ?p ?o }"})
        self.assertEqual(len(sent), 3)
        self.assertEqual(planes.requests_sent(), 3)

    def test_refusal_is_not_swallowed_into_a_verdict(self):
        # blocker_state turns any Exception from its ASK into UNKNOWN.
        self.assertFalse(issubclass(planes.RequestBudgetExceeded, Exception))

    def test_uncapped_call_still_reports_exact_cost(self):
        store = Keyset(blocked_store().post)
        with wire(store) as sent:
            out = ca.run("blocked", {"now": NOW, "items": ["w1"], "changes": []}, post)
        self.assertGreater(len(sent), 0)
        self.assertEqual(out["cost"], {"quipu_requests": len(sent)})
        self.assertEqual([r["item"] for r in out["records"]], ["w1"])

    def test_capped_evaluation_raises_instead_of_exceeding(self):
        with wire(Keyset(blocked_store().post)) as sent, self.assertRaises(planes.RequestBudgetExceeded):
            ca.run("blocked", {"now": NOW, "items": ["w1"], "changes": [], "budget": {"max_requests": 2}}, post)
        self.assertEqual(len(sent), 2)

    def test_capped_call_within_budget_reports_exact_cost(self):
        with wire(Keyset(blocked_store().post)) as sent:
            out = ca.run("blocked", {"now": NOW, "route": [], "budget": {"max_requests": 1}}, post)
        self.assertEqual((out["items"], out["cost"]["quipu_requests"], len(sent)), ([], 0, 0))

    def test_malformed_budget_is_refused(self):
        for bad in ({}, {"max_requests": 0}, {"max_requests": True}, {"max_requests": 1.5},
                    {"max_requests": 2, "max_seconds": 1}, 5):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                ca.run("blocked", {"now": NOW, "route": [], "budget": bad}, post)

    def test_describe_declares_both_contracts(self):
        for kind in ("blocked", "review", "directive", "directive-lapse"):
            with self.subTest(kind=kind):
                self.assertEqual({k: ca.description(kind)[k] for k in ca.CONTRACTS},
                                 {"budget_cap": 1, "reconcile_contract": 1})


class PagedDiscovery(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.multiple(ca, PAGE=3, OBS_PAGE=4, OBS_UNIT=2)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(planes.start_request_budget, None)

    def assertPinned(self, sent):
        queries = [b for b in sent if "query" in b]
        self.assertTrue(queries)
        self.assertEqual({b.get("tx") for b in queries}, {W})

    def test_blocked_pages_cover_every_candidate_exactly_once(self):
        pages, sent = drain("blocked", Keyset(blocked_store().post), 30)
        items = [i for p in pages for i in p["items"]]
        self.assertGreater(len(pages), 2)
        self.assertEqual(len(items), len(set(items)))
        self.assertEqual(sorted(items), sorted([f"w{i}" for i in range(7)] + [f"p{i}" for i in range(5)]))
        self.assertPinned(sent)
        self.assertTrue(all("next_cursor" in p for p in pages[:-1]))
        self.assertNotIn("next_cursor", pages[-1])

    def test_paging_is_deterministic_and_independent_of_budget(self):
        small = [i for p in drain("blocked", Keyset(blocked_store().post), 30)[0] for i in p["items"]]
        large = [i for p in drain("blocked", Keyset(blocked_store().post), 10000)[0] for i in p["items"]]
        self.assertEqual(small, large)

    def test_review_pages_union_ages_and_live_memories_once(self):
        inferred = memory("m1") + memory("m2") + [("stale", "naturalKey", "key")]
        root = [(f"f{i}", "reviewAfter", "2026-10-01T00:00:00Z") for i in range(5)]
        root += [("m1", "maxAge", "P1D")]  # an age AND a memory: once
        store = Keyset(MemoryStore(inferred, root).post)
        pages, sent = drain("review", store, 10)
        items = [i for p in pages for i in p["items"]]
        self.assertEqual(len(items), len(set(items)))
        self.assertEqual(sorted(items), sorted([f"f{i}" for i in range(5)] + ["m1", "m2"]))
        self.assertGreater(len(pages), 1)
        self.assertPinned(sent)
        legacy = ca.evaluate("review", MemoryStore(inferred, root).post, {"discover": True, "now": NOW})
        self.assertEqual(sorted(items), legacy["items"])

    def test_directive_pages(self):
        quipu = FakeQuipu()
        for i in range(8):
            quipu.add(bb.A + f"dir{i}", "a", bb.A + "Directive")
        quipu.add(bb.A + "pol", "a", bb.A + "Policy")
        for kind in ("directive", "directive-lapse"):
            pages, sent = drain(kind, Keyset(quipu), 1)
            self.assertEqual([i for p in pages for i in p["items"]], [f"dir{i}" for i in range(8)])
            self.assertPinned(sent)

    def test_a_budget_that_fits_no_unit_fails(self):
        with wire(Keyset(blocked_store().post)), self.assertRaises(planes.RequestBudgetExceeded):
            ca.run("blocked", {"discover": True, "now": NOW, "as_of_tx": W, "cursor": None,
                               "budget": {"max_requests": 1}}, post)

    def test_cursor_is_bound_to_its_snapshot_and_adapter(self):
        pages, _ = drain("blocked", Keyset(blocked_store().post), 30)
        cursor = pages[0]["next_cursor"]
        with wire(Keyset(blocked_store().post)):
            for request in ({"as_of_tx": W + 1, "cursor": cursor}, {"as_of_tx": W, "cursor": "%%%"},
                            {"as_of_tx": W, "cursor": 7}, {"as_of_tx": -1, "cursor": None},
                            {"as_of_tx": "4242", "cursor": None}):
                with self.subTest(request=request), self.assertRaises(ValueError):
                    ca.run("blocked", {"discover": True, "now": NOW, **request}, post)
            with self.assertRaises(ValueError):
                ca.run("review", {"discover": True, "now": NOW, "as_of_tx": W, "cursor": cursor}, post)

    def test_legacy_discover_is_unchanged(self):
        store = blocked_store()
        recorded = []
        def legacy(endpoint, body):
            recorded.append(body)
            return store.post(endpoint, body)
        with wire(legacy) as sent:
            out = ca.run("blocked", {"discover": True, "now": NOW}, post)
        # Legacy keeps owners of ANY blocker-bearing Observation (a superset).
        self.assertEqual(out["items"], sorted([f"w{i}" for i in range(7)] + [f"p{i}" for i in range(5)] + ["gone"]))
        self.assertEqual(set(out), {"version", "items", "cost"})
        self.assertEqual(out["cost"]["quipu_requests"], len(sent))
        self.assertFalse(any("tx" in b for b in recorded))
        self.assertEqual({k: v for k, v in out.items() if k != "cost"},
                         ca.evaluate("blocked", store.post, {"discover": True, "now": NOW}))

    def test_batched_latest_matches_single_item_reads(self):
        store = blocked_store()
        names = ["gone", "w0", "w3"] + [f"p{i}" for i in range(5)] + ["absent"]
        self.assertEqual(bb.latest_observations(store.post, names),
                         {n: bb.latest_observation(store.post, n) for n in names})


class _Quipu(http.server.BaseHTTPRequestHandler):
    seen = []

    def do_POST(self):
        self.seen.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        raw = json.dumps({"rows": [], "truncated": False}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


class Process(unittest.TestCase):
    """The adapter process against a loopback fake: nonzero exit at the cap."""

    def setUp(self):
        _Quipu.seen = []
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Quipu)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def run_adapter(self, request):
        env = {**os.environ, "QUIPU_SERVER": f"http://127.0.0.1:{self.server.server_port}",
               "QUIPU_AUTH_TOKEN": "", "QUIPU_AUTH_TOKEN_FILE": os.devnull}
        return subprocess.run([sys.executable, str(ROOT / "scripts/blocked_by.py"), "--changes"],
                              input=json.dumps(request), capture_output=True, text=True, env=env, timeout=60)

    def test_cap_exits_nonzero_without_exceeding(self):
        done = self.run_adapter({"discover": True, "now": NOW, "budget": {"max_requests": 1}})
        self.assertEqual(done.returncode, 3, done.stderr)
        self.assertEqual(done.stdout, "")
        self.assertIn("request budget exhausted", done.stderr)
        self.assertEqual(len(_Quipu.seen), 1)

    def test_cost_equals_requests_the_server_received(self):
        done = self.run_adapter({"discover": True, "now": NOW, "as_of_tx": W, "cursor": None,
                                 "budget": {"max_requests": 50}})
        self.assertEqual(done.returncode, 0, done.stderr)
        out = json.loads(done.stdout)
        self.assertEqual(out["items"], [])
        self.assertTrue(out["complete"])
        self.assertEqual(out["cost"], {"quipu_requests": len(_Quipu.seen)})
        self.assertEqual({b["tx"] for b in _Quipu.seen}, {W})


if __name__ == "__main__":
    unittest.main()
