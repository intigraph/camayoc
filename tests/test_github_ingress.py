"""GitHub activity enters through one ingress as governed records (aegis-c01c.3).

Replays RECORDED deliveries (tests/fixtures/github/README.md says where each
came from) through the same spool and drain the receiver and the poller use,
so the replay exercises the production path, not a parallel one. The secret
below is a test value; no real secret is read anywhere in this file.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import http.client
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from quipu_bin_guard import QUIPU, requires_quipu

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("github_ingress", ROOT / "scripts/github_ingress.py")
gi = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(gi)

FIX = ROOT / "tests/fixtures/github"
DELIVERIES = FIX / "deliveries"
POLL = FIX / "poll"
SECRET = b"test-secret-not-a-real-credential"
WINDOW = "https://camayoc.local/window/github/2026-10"


def delivery(name: str) -> dict:
    return json.loads((DELIVERIES / f"{name}.json").read_text())


def body_bytes(rec: dict) -> bytes:
    return json.dumps(rec["body"], sort_keys=True).encode()


def episode(name: str, **kw) -> dict:
    rec = delivery(name)
    return gi.episode_for(rec["headers"]["X-GitHub-Event"], rec["body"], actor="tester", **kw)[0]


def activity(body: dict) -> dict:
    return body["nodes"][0]["properties"]


class Collect:
    def __init__(self, fail: int = 0):
        self.bodies: list[dict] = []
        self.fail = fail

    def __call__(self, body: dict) -> None:
        if self.fail:
            self.fail -= 1
            raise OSError("quipu gave no answer")
        self.bodies.append(body)


def replay(spool=None) -> tuple[dict, list[dict]]:
    writer = Collect()
    counts = gi._replay(DELIVERIES, actor="tester", writer=writer, spool=spool or gi.Spool(":memory:"))
    return counts, writer.bodies


class MappingTests(unittest.TestCase):
    def test_admitted_kinds_are_exactly_the_shape_closed_set(self):
        shapes = (ROOT / "shapes/github.shapes.ttl").read_text()
        block = shapes[shapes.index("sh:path aegis:githubEventKind ; sh:minCount 1 ; sh:maxCount 1 ; sh:in ("):]
        block = block[:block.index(") ]")]
        in_shape = set(re.findall(r'"([a-z_.]+)"', block))
        mapping, _ = gi.load_mapping()
        self.assertEqual(in_shape, gi.admitted_kinds(mapping))

    def test_pull_request_closed_merged(self):
        body = episode("pull_request.closed")
        props = activity(body)
        self.assertEqual("pull_request.closed", props["githubEventKind"])
        self.assertEqual("closed", props["githubState"])
        self.assertIs(True, props["githubMerged"])
        self.assertEqual("24091efeed09250206b3f3295bc7a31867c5b2c2", props["githubMergeCommitSha"])
        self.assertEqual("256a7e1433679ba0ccbff647d46738a65fdfc6af", props["commitSha"])
        self.assertEqual("2026-10-27T18:56:18Z", props["occurredAt"])
        self.assertEqual("main", props["githubBaseRef"])
        self.assertEqual(WINDOW, body["graph"])
        self.assertEqual("github", body["group_id"])
        pr = body["nodes"][1]
        self.assertEqual(("github.PR_kwDOTvaTnM8AAAABLF8ZkA", "GitHubPullRequest", 177),
                         (pr["name"], pr["type"], pr["properties"]["githubNumber"]))
        self.assertIn({"source": pr["name"], "target": "github.R_kgDOTvaTnA", "relation": "inGitHubRepository"},
                      body["edges"])
        self.assertIn({"source": body["nodes"][0]["name"], "target": pr["name"], "relation": "aboutGitHubEntity"},
                      body["edges"])

    def test_opened_keeps_mentions_and_never_the_body(self):
        body = episode("pull_request.opened")
        props = activity(body)
        self.assertEqual(("open", False), (props["githubState"], props["githubMerged"]))
        self.assertEqual(["sattler-bot", "scbrown"], props["githubMentions"])
        self.assertNotIn("Refuse oversize", json.dumps(body))

    def test_review_on_its_pull_request(self):
        body = episode("pull_request_review.submitted")
        props = activity(body)
        self.assertEqual("approved", props["reviewState"])
        review, repo, pr = body["nodes"][1], body["nodes"][2], body["nodes"][3]
        self.assertEqual(("GitHubReview", "GitHubRepository", "GitHubPullRequest"), (review["type"], repo["type"], pr["type"]))
        self.assertIn({"source": review["name"], "target": pr["name"], "relation": "onGitHubThread"}, body["edges"])

    def test_issue_comment_on_a_pr_thread_ignores_code_mentions(self):
        body = episode("issue_comment.created")
        self.assertEqual(["donna-box"], activity(body)["githubMentions"])
        self.assertEqual("GitHubPullRequest", body["nodes"][3]["type"])  # issue.pull_request present

    def test_runs_checks_push_release(self):
        wr = activity(episode("workflow_run.completed"))
        self.assertEqual(("completed", "success", 1, "main"),
                         (wr["githubStatus"], wr["githubConclusion"], wr["githubRunAttempt"], wr["githubBaseRef"]))
        cr = activity(episode("check_run.completed"))
        self.assertEqual(("completed", "success"), (cr["githubStatus"], cr["githubConclusion"]))
        push = episode("push")
        self.assertEqual("2026-10-27T14:25:37Z", activity(push)["occurredAt"])  # epoch pushed_at
        self.assertEqual("refs/heads/main", push["nodes"][1]["properties"]["githubRef"])
        rel = episode("release.published")
        self.assertEqual("v0.1.3", rel["nodes"][1]["properties"]["githubTagName"])

    def test_ping_is_ignored_and_unknowns_are_quarantined(self):
        with self.assertRaises(gi.Ignored):
            episode("ping")
        with self.assertRaisesRegex(gi.Quarantined, "no mapping for GitHub event 'star'"):
            episode("star.created")
        rec = delivery("pull_request.closed")
        rec["body"]["action"] = "labeled"
        with self.assertRaisesRegex(gi.Quarantined, "action 'labeled'"):
            gi.episode_for("pull_request", rec["body"], actor="t")
        trimmed = copy.deepcopy(rec["body"])
        trimmed["action"] = "closed"
        trimmed["pull_request"] = {"id": 1, "number": 177, "url": gi.API + "/repos/intigraph/camayoc/pulls/177"}
        with self.assertRaises(gi.MissingField) as caught:
            gi.episode_for("pull_request", trimmed, actor="t")
        self.assertEqual(gi.API + "/repos/intigraph/camayoc/pulls/177", caught.exception.hydrate)
        bad = copy.deepcopy(rec["body"])
        bad["action"] = "closed"
        bad["pull_request"]["closed_at"] = "2026-02-30T00:00:00Z"
        with self.assertRaisesRegex(gi.Quarantined, "calendar-valid"):
            gi.episode_for("pull_request", bad, actor="t")

    def test_legacy_node_ids_are_hashed_not_sanitized(self):
        a, b = gi.node_name("MDEwOlJlcG9zaXRvcnkx+/="), gi.node_name("MDEwOlJlcG9zaXRvcnkx/+=")
        self.assertNotEqual(a, b)
        self.assertTrue(a.startswith("github.b"))
        self.assertEqual("github.PR_kw-1", gi.node_name("PR_kw-1"))

    def test_mentions(self):
        self.assertEqual(["a-b", "c"], gi.mentions("hi @A-b and @c, mail x@y.z, `@code` ```\n@block\n```", "@c"))
        self.assertEqual([], gi.mentions(None, "@-x @x-"))


class ReplayTests(unittest.TestCase):
    def test_recorded_deliveries_end_to_end(self):
        counts, bodies = replay()
        self.assertEqual({"written": 8, "ignored": 1, "quarantined": 1}, counts)
        self.assertEqual(8, len({b["name"] for b in bodies}))

    def test_replay_is_byte_identical(self):
        _, first = replay()
        _, second = replay()
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))

    def test_redelivery_is_spooled_once(self):
        spool = gi.Spool(":memory:")
        rec = delivery("pull_request.closed")
        self.assertEqual("accepted", spool.add(rec["headers"]["X-GitHub-Delivery"], "pull_request", body_bytes(rec)))
        self.assertEqual("duplicate", spool.add(rec["headers"]["X-GitHub-Delivery"], "pull_request", body_bytes(rec)))
        writer = Collect()
        self.assertEqual({"written": 1}, gi.drain_once(spool, actor="t", writer=writer))
        self.assertEqual({}, gi.drain_once(spool, actor="t", writer=writer))

    def test_failed_write_stays_pending_then_lands(self):
        spool = gi.Spool(":memory:")
        rec = delivery("push")
        spool.add("d1", "push", body_bytes(rec), now=0)
        writer = Collect(fail=1)
        self.assertEqual({"retry": 1}, gi.drain_once(spool, actor="t", writer=writer, now=1))
        self.assertEqual({}, gi.drain_once(spool, actor="t", writer=writer, now=2))  # backing off
        self.assertEqual({"written": 1}, gi.drain_once(spool, actor="t", writer=writer, now=100))
        self.assertEqual({"written": 1}, spool.counts())

    def test_unwatched_repository_is_not_written(self):
        spool = gi.Spool(":memory:")
        spool.add("d1", "push", body_bytes(delivery("push")))
        self.assertEqual({"foreign": 1}, gi.drain_once(spool, actor="t", writer=Collect(), repos={"other/repo"}))


class SignatureTests(unittest.TestCase):
    def test_github_documented_vector(self):
        # docs.github.com "Validating webhook deliveries", test values.
        self.assertTrue(gi.verify_signature(
            b"It's a Secret to Everybody",
            "sha256=757107ea0eb2509fc211221cce984b8a37570b6d7586c22c46f4379c8b043e17", b"Hello, World!"))

    def test_refusals(self):
        sig = "sha256=" + hmac.new(SECRET, b"x", hashlib.sha256).hexdigest()
        self.assertTrue(gi.verify_signature(SECRET, sig, b"x"))
        self.assertFalse(gi.verify_signature(SECRET, sig, b"y"))
        self.assertFalse(gi.verify_signature(b"", sig, b"x"))  # no secret: fail closed
        self.assertFalse(gi.verify_signature(SECRET, None, b"x"))
        self.assertFalse(gi.verify_signature(SECRET, sig.replace("sha256=", "sha1="), b"x"))


class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.spool = gi.Spool(":memory:")
        self.wake = threading.Event()
        handler = gi.make_handler(self.spool, SECRET, {"intigraph/camayoc"}, "/hooks/github", self.wake)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def post(self, rec: dict, *, body: bytes | None = None, secret: bytes = SECRET, path: str = "/hooks/github",
             length: str | None = None) -> tuple[int, dict]:
        body = body_bytes(rec) if body is None else body
        headers = dict(rec["headers"])
        headers["X-Hub-Signature-256"] = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
        if length is not None:
            headers["Content-Length"] = length
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        conn.request("POST", path, body=body, headers=headers)
        resp = conn.getresponse()
        out = (resp.status, json.loads(resp.read() or b"{}"))
        conn.close()
        return out

    def test_accept_then_duplicate(self):
        rec = delivery("pull_request.closed")
        self.assertEqual((202, "accepted"), (lambda r: (r[0], r[1]["outcome"]))(self.post(rec)))
        self.assertEqual((200, "duplicate"), (lambda r: (r[0], r[1]["outcome"]))(self.post(rec)))
        self.assertTrue(self.wake.is_set())
        self.assertEqual({"pending": 1}, self.spool.counts())

    def test_refusals_spool_nothing(self):
        rec = delivery("pull_request.closed")
        self.assertEqual(401, self.post(rec, secret=b"wrong")[0])
        self.assertEqual(404, self.post(rec, path="/other")[0])
        self.assertEqual(413, self.post(rec, length=str(gi.MAX_BODY + 1))[0])
        foreign = copy.deepcopy(rec)
        foreign["body"]["repository"]["full_name"] = "someone/else"
        self.assertEqual((202, "ignored"), (lambda r: (r[0], r[1]["outcome"]))(self.post(foreign)))
        self.assertEqual((200, "pong"), (lambda r: (r[0], r[1]["outcome"]))(self.post(delivery("ping"))))
        self.assertEqual({}, self.spool.counts())


class FakeTransport:
    """Serves the recorded poll fixtures; counts requests; ETag 304 on repeat."""

    def __init__(self, remaining: int = 4000):
        self.remaining = remaining
        self.calls: list[str] = []
        self.files = {
            "/repos/intigraph/camayoc": "repo.json",
            "/repos/intigraph/camayoc/events?per_page=100": "events-page.json",
            "/repos/intigraph/camayoc/actions/runs?per_page=20": "runs-page.json",
            "/repos/intigraph/camayoc/pulls/177": "pull-177.json",
            "/repos/intigraph/camayoc/pulls/172": "pull-172.json",
        }

    def get(self, path, etag=None):
        self.calls.append(path)
        if path == "/rate_limit":
            return 200, {}, json.dumps({"resources": {"core": {"remaining": self.remaining}}}).encode()
        name = self.files.get(path)
        if name is None:
            return 404, {}, b'{"message": "Not Found"}'
        tag = 'W/"' + hashlib.sha256(name.encode()).hexdigest()[:16] + '"'
        if etag == tag:
            return 304, {"x-poll-interval": "60"}, b""
        return 200, {"etag": tag, "x-poll-interval": "60"}, (POLL / name).read_bytes()


class PollerTests(unittest.TestCase):
    def poller(self, transport, spool=None, **kw):
        return gi.Poller(spool or gi.Spool(":memory:"), transport, ["intigraph/camayoc"], **kw)

    def test_cycle_hydrates_spools_and_then_304s(self):
        t = FakeTransport()
        p = self.poller(t)
        first = p.cycle(now=1000)
        self.assertNotIn("skipped", first)
        # 2 PR, 2 push, 2 review, 2 comment events and 1 run; DeleteEvent is not mapped.
        self.assertEqual(9, first["accepted"])
        # Hydration: each object once per cycle, though two events name PR 177.
        self.assertEqual(1, t.calls.count("/repos/intigraph/camayoc/pulls/177"))
        self.assertIn("/repos/intigraph/camayoc/pulls/140", t.calls)  # recorded 404: stays trimmed
        second = p.cycle(now=1100)
        self.assertEqual(2, second["not_modified"])
        self.assertNotIn("accepted", second)
        drained = gi.drain_once(p.spool, actor="t", writer=Collect(), repos={"intigraph/camayoc"}, now=1200)
        self.assertEqual(0, drained.get("retry", 0))
        self.assertGreaterEqual(drained["written"], 6)

    def test_webhook_and_poll_of_one_change_write_once(self):
        spool = gi.Spool(":memory:")
        rec = delivery("pull_request.closed")
        spool.add(rec["headers"]["X-GitHub-Delivery"], "pull_request", body_bytes(rec), now=0)
        self.poller(FakeTransport(), spool).cycle(now=1)
        writer = Collect()
        counts = gi.drain_once(spool, actor="t", writer=writer, repos={"intigraph/camayoc"}, now=2)
        closed = [b for b in writer.bodies if activity(b)["githubEventKind"] == "pull_request.closed"]
        self.assertEqual(1, len(closed))
        self.assertGreaterEqual(counts["duplicate"], 1)
        self.assertEqual("2026-10-27T18:56:18Z", activity(closed[0])["occurredAt"])

    def test_poll_keeps_the_events_state_not_the_hydrated_one(self):
        spool = gi.Spool(":memory:")
        self.poller(FakeTransport(), spool).cycle(now=1)
        writer = Collect()
        gi.drain_once(spool, actor="t", writer=writer, now=2)
        opened = [activity(b) for b in writer.bodies if activity(b)["githubEventKind"] == "pull_request.opened"]
        self.assertEqual(1, len(opened))
        self.assertEqual(("open", False), (opened[0]["githubState"], opened[0]["githubMerged"]))
        self.assertNotIn("githubDraft", opened[0])

    def test_budget_and_reserve(self):
        t = FakeTransport(remaining=900)
        receipt = self.poller(t, reserve=1000).cycle(now=1)
        self.assertEqual("below reserve", receipt["skipped"])
        self.assertEqual(["/rate_limit"], t.calls)
        t = FakeTransport()
        receipt = self.poller(t, budget_per_hour=2).cycle(now=1)
        self.assertTrue(receipt["over_budget"])
        self.assertEqual(2, receipt["requests"])

    def test_failed_hydration_is_quarantined_with_its_reason(self):
        spool = gi.Spool(":memory:")
        self.poller(FakeTransport(), spool).cycle(now=1)
        gi.drain_once(spool, actor="t", writer=Collect(), now=2)
        reasons = [r[0] for r in spool.conn.execute("SELECT reason FROM deliveries WHERE state='quarantined'")]
        self.assertTrue(reasons)
        self.assertTrue(all("pull_request" in r for r in reasons), reasons)


@requires_quipu
class QuipuRoundTripTests(unittest.TestCase):
    """The replayed episodes in a real store: shapes hold, re-ingest is a no-op,
    and the stored queries answer."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = str(Path(cls.tmp.name) / "s.db")
        subprocess.run([str(QUIPU), "graph", "create", WINDOW, "--db", cls.db], check=True, capture_output=True)
        _, cls.bodies = replay()
        cls.outputs = []
        for i, body in enumerate(cls.bodies + cls.bodies[:1]):
            path = Path(cls.tmp.name) / f"ep{i}.json"
            path.write_text(json.dumps(body))
            out = subprocess.run([str(QUIPU), "episode", str(path), "--db", cls.db,
                                  "--timestamp", "2026-10-28T00:00:00Z"], capture_output=True, text=True, check=True)
            cls.outputs.append(out.stdout)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def read(self, sparql: str) -> str:
        return subprocess.run([str(QUIPU), "read", sparql, "--db", self.db], capture_output=True, text=True,
                              check=True).stdout

    def test_exported_window_conforms_to_the_shapes(self):
        ttl = subprocess.run([str(QUIPU), "export", "--graph", WINDOW, "--format", "turtle", "--db", self.db],
                             capture_output=True, text=True, check=True).stdout
        data = Path(self.tmp.name) / "window.ttl"
        data.write_text(ttl)
        out = subprocess.run([str(QUIPU), "validate", "--shapes", str(ROOT / "shapes/github.shapes.ttl"),
                              "--data", str(data)], capture_output=True, text=True)
        self.assertTrue(out.stdout.lstrip().startswith("valid"), out.stdout + out.stderr)
        self.assertEqual(8, ttl.count("aegis:GitHubActivity") or ttl.count("GitHubActivity>"))

    def test_reingest_writes_nothing(self):
        self.assertIn("0 facts (tx 0)", self.outputs[-1])

    def stored(self, name: str, **params: str) -> str:
        template = json.loads((ROOT / "queries" / f"{name}.json").read_text())["template"]
        for key, value in params.items():
            template = template.replace("{" + key + "}", value)
        return self.read(template)

    def test_stored_queries_answer(self):
        state = self.stored("camayoc_github_pr_current_state", repo="intigraph/camayoc", number="177")
        self.assertIn('"pull_request.closed"', state)
        self.assertIn("1 results", state)
        ci = self.stored("camayoc_github_default_branch_ci", repo="intigraph/camayoc", branch="main")
        self.assertIn('"ci"\t"success"', ci)
        threads = self.stored("camayoc_github_participating_threads", login="donna-box")
        self.assertIn("github.PR_kwDOTvaTnM8AAAABKYAZcg", threads)
        self.assertIn("1 results", threads)


if __name__ == "__main__":
    unittest.main()
