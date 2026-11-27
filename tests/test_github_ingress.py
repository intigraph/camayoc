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
import sqlite3
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
GRAPH = "https://camayoc.local/operational/github"


def delivery(name: str) -> dict:
    return json.loads((DELIVERIES / f"{name}.json").read_text())


def body_bytes(rec: dict) -> bytes:
    """What a webhook sends on the wire (the receiver signs and redacts this)."""
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
        self.assertEqual(GRAPH, body["graph"])
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

    def test_recruitment_inbox_only_records_verified_webhooks_atomically(self):
        spool = gi.Spool(":memory:")
        payload = delivery("check_run.completed")["body"]
        spool.add("poll-row", "check_run", payload)
        spool.add("webhook-row", "check_run", payload, verified_webhook=True)
        spool.add("webhook-row", "check_run", payload, verified_webhook=True)
        rows = spool.conn.execute("SELECT delivery,event FROM recruitment_events").fetchall()
        self.assertEqual([("webhook-row", "check_run")], rows)
        spool.conn.execute("CREATE TRIGGER refuse_recruit BEFORE INSERT ON recruitment_events "
                           "BEGIN SELECT RAISE(ABORT, 'test disk failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            spool.add("failed-row", "check_run", payload, verified_webhook=True)
        self.assertFalse(spool.has("failed-row"))

    def test_check_suite_completed_survives_redaction_and_deduplicates(self):
        payload = copy.deepcopy(delivery("check_run.completed")["body"])
        run = payload.pop("check_run")
        payload["check_suite"] = {k: v for k, v in run.items()
                                  if k in ("node_id", "id", "head_sha", "status", "conclusion")}
        payload["check_suite"].update(updated_at="2026-11-21T12:00:00Z",
                                     head_branch="main", secret="discard this")
        redacted = gi.redact(payload)
        self.assertNotIn("secret", redacted["check_suite"])
        before, key = gi.episode_for("check_suite", payload, actor="tester")
        after, after_key = gi.episode_for("check_suite", redacted, actor="tester")
        self.assertEqual((before, key), (after, after_key))
        self.assertEqual("GitHubCheckSuite", after["nodes"][1]["type"])
        self.assertEqual("check_suite.completed", activity(after)["githubEventKind"])
        spool = gi.Spool(":memory:")
        spool.add("suite-one", "check_suite", redacted)
        spool.add("suite-two", "check_suite", redacted)
        writer = Collect()
        counts = gi.drain_once(spool, actor="tester", writer=writer)
        self.assertEqual({"written": 1, "duplicate": 1}, counts)
        self.assertEqual(1, len(writer.bodies))
        redacted["check_suite"]["head_sha"] = "invalid"
        with self.assertRaises(gi.Quarantined):
            gi.episode_for("check_suite", redacted, actor="tester")

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

    def test_repeat_workflow_in_progress_is_ignored_first_and_terminal_kept(self):
        """aegis-n0wc: workflow_run.in_progress repeats the pending state that
        requested already recorded; it is acknowledged without a write. The
        first non-terminal transition of each run and every completed stay."""
        def run(action, status, conclusion=None):
            body = copy.deepcopy(delivery("workflow_run.completed")["body"])
            body["action"] = action
            body["workflow_run"].update(status=status, conclusion=conclusion)
            return body
        with self.assertRaisesRegex(gi.Ignored, "workflow_run.in_progress"):
            gi.episode_for("workflow_run", run("in_progress", "in_progress"), actor="t")
        requested = activity(gi.episode_for("workflow_run", run("requested", "queued"), actor="t")[0])
        self.assertEqual("queued", requested["githubStatus"])
        created = copy.deepcopy(delivery("check_run.completed")["body"])
        created["action"] = "created"
        created["check_run"].update(status="queued", conclusion=None)
        self.assertEqual("queued", activity(gi.episode_for("check_run", created, actor="t")[0])["githubStatus"])
        self.assertEqual("completed", activity(episode("workflow_run.completed"))["githubStatus"])
        # Through the drain: settled 'ignored', nothing written, nothing retried.
        spool = gi.Spool(":memory:")
        spool.add("ip", "workflow_run", run("in_progress", "in_progress"))
        writer = Collect()
        self.assertEqual({"ignored": 1}, gi.drain_once(spool, actor="t", writer=writer))
        self.assertEqual([], writer.bodies)
        self.assertEqual({}, gi.drain_once(spool, actor="t", writer=writer))

    def test_ping_is_ignored_and_unknowns_are_quarantined(self):
        with self.assertRaises(gi.Ignored):
            episode("ping")
        with self.assertRaisesRegex(gi.Quarantined, "no mapping for GitHub event 'star'"):
            episode("star.created")
        rec = delivery("pull_request.closed")
        rec["body"]["action"] = "assigned"
        with self.assertRaisesRegex(gi.Quarantined, "action 'assigned'"):
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

    def test_reviewed_ignored_actions_settle_ignored_not_quarantined(self):
        # aegis-ov41: ~130 deliveries of these actions were quarantined in
        # production. They carry no modelled fact, so the mapping records the
        # decision to acknowledge them without a write.
        mapping, _ = gi.load_mapping()
        for event, actions in {"pull_request": ["labeled", "unlabeled", "enqueued", "dequeued"],
                               "release": ["created"]}.items():
            self.assertEqual(actions, mapping["events"][event]["ignored"])
            for action in actions:
                body = {"action": action}
                with self.assertRaisesRegex(gi.Ignored, f"{event}.{action}"):
                    gi.episode_for(event, body, actor="t")
        # An ignored action is never also admitted (the two lists are disjoint).
        for event, rule in mapping["events"].items():
            self.assertFalse(set(rule.get("ignored", ())) & set(rule["actions"] or ()), event)
        # Through the drain: settled 'ignored', nothing written, not retried.
        spool = gi.Spool(":memory:")
        rec = delivery("pull_request.closed")
        rec["body"]["action"] = "labeled"
        spool.add("lb", "pull_request", rec["body"])
        writer = Collect()
        self.assertEqual({"ignored": 1}, gi.drain_once(spool, actor="t", writer=writer))
        self.assertEqual([], writer.bodies)

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
        self.assertEqual("accepted", spool.add(rec["headers"]["X-GitHub-Delivery"], "pull_request", rec["body"]))
        self.assertEqual("duplicate", spool.add(rec["headers"]["X-GitHub-Delivery"], "pull_request", rec["body"]))
        writer = Collect()
        self.assertEqual({"written": 1}, gi.drain_once(spool, actor="t", writer=writer))
        self.assertEqual({}, gi.drain_once(spool, actor="t", writer=writer))

    def test_repository_metadata_is_asserted_once_not_per_delivery(self):
        """aegis-xxoa.5.1: 12,500 deliveries re-asserted the same 7 repository
        values under 12,500 sources. Across N deliveries the repository node
        rides in ONE body; every body still names it by IRI in its edges."""
        _, bodies = replay()
        repo = "github.R_kgDOTvaTnA"
        carrying = [b for b in bodies if any(n["type"] == "GitHubRepository" for n in b["nodes"])]
        self.assertEqual(1, len(carrying))
        self.assertGreater(len(bodies), 1)
        for b in bodies:
            self.assertTrue(any(e["target"] == repo and e["relation"] == "inGitHubRepository"
                                for e in b["edges"]), b["name"])

    def test_changed_repository_metadata_is_written_again(self):
        spool = gi.Spool(":memory:")
        first, second = delivery("push")["body"], delivery("pull_request.closed")["body"]
        changed = copy.deepcopy(delivery("check_run.completed")["body"])
        changed["repository"]["html_url"] = "https://github.com/intigraph/camayoc-renamed"
        for i, (event, payload) in enumerate((("push", first), ("pull_request", second),
                                              ("check_run", changed))):
            spool.add(f"d{i}", event, payload, now=i)
        writer = Collect()
        self.assertEqual({"written": 3}, gi.drain_once(spool, actor="t", writer=writer, now=10))
        repos = [[n for n in b["nodes"] if n["type"] == "GitHubRepository"] for b in writer.bodies]
        self.assertEqual([1, 0, 1], [len(r) for r in repos])
        self.assertEqual("https://github.com/intigraph/camayoc-renamed", repos[2][0]["properties"]["githubUrl"])

    def test_a_failed_write_does_not_mark_the_repository_written(self):
        spool = gi.Spool(":memory:")
        spool.add("d1", "push", delivery("push")["body"], now=0)
        self.assertEqual({"retry": 1}, gi.drain_once(spool, actor="t", writer=Collect(fail=1), now=1))
        self.assertIsNone(spool.repo_asserted("github.R_kgDOTvaTnA"))
        writer = Collect()
        self.assertEqual({"written": 1}, gi.drain_once(spool, actor="t", writer=writer, now=1000))
        self.assertTrue(any(n["type"] == "GitHubRepository" for n in writer.bodies[0]["nodes"]))

    def test_refusal_without_repository_retries_with_it(self):
        """If the store refuses a body that relied on the repository already
        being there (rebuilt store), the next attempt asserts it in full;
        only a full body's refusal is quarantined as permanent."""
        spool = gi.Spool(":memory:")
        spool.add("d0", "push", delivery("push")["body"], now=0)
        gi.drain_once(spool, actor="t", writer=Collect(), now=1)
        spool.add("d1", "pull_request", delivery("pull_request.closed")["body"], now=2)
        seen: list[dict] = []

        def refuse(body):
            seen.append(body)
            raise gi.EpisodeRefused("sh:class GitHubRepository")
        self.assertEqual({"retry": 1}, gi.drain_once(spool, actor="t", writer=refuse, now=3))
        self.assertFalse(any(n["type"] == "GitHubRepository" for n in seen[0]["nodes"]))
        self.assertIsNone(spool.repo_asserted("github.R_kgDOTvaTnA"))
        writer = Collect()
        self.assertEqual({"written": 1}, gi.drain_once(spool, actor="t", writer=writer, now=1000))
        self.assertTrue(any(n["type"] == "GitHubRepository" for n in writer.bodies[0]["nodes"]))
        self.assertIsNotNone(spool.repo_asserted("github.R_kgDOTvaTnA"))

    def test_failed_write_stays_pending_then_lands(self):
        spool = gi.Spool(":memory:")
        rec = delivery("push")
        spool.add("d1", "push", rec["body"], now=0)
        writer = Collect(fail=1)
        self.assertEqual({"retry": 1}, gi.drain_once(spool, actor="t", writer=writer, now=1))
        self.assertEqual({}, gi.drain_once(spool, actor="t", writer=writer, now=2))  # backing off
        self.assertEqual({"written": 1}, gi.drain_once(spool, actor="t", writer=writer, now=100))
        self.assertEqual({"written": 1}, spool.counts())

    def test_http_400_refusal_is_quarantined_not_retried_forever(self):
        """aegis-n0wc: a 400 from the /episode POST refuses these exact bytes;
        quarantine with the reason. A 400 from /graph/create or /graph/label
        (wu, PR #261), and 401/403/408/429/5xx or transport errors on any
        call, keep the existing retry. Drives the real Writer."""
        import io
        import urllib.error
        from unittest import mock
        import planes

        def post_failing(path, code):
            def _post(endpoint, body, **kwargs):
                if endpoint != path:
                    return {}
                try:
                    raise urllib.error.HTTPError("http://q" + endpoint, code, "x", {},
                                                 io.BytesIO(b'{"error":"refused"}'))
                except urllib.error.HTTPError as e:
                    raise planes.PlaneError(f"{endpoint} failed: HTTP {code}") from e
            return _post

        def spool_with(n):
            spool = gi.Spool(":memory:")
            for i, name in enumerate(("push", "pull_request.closed", "check_run.completed")[:n]):
                rec = delivery(name)
                spool.add(f"d{i}", rec["headers"]["X-GitHub-Event"], rec["body"], now=0)
            return spool

        spool = spool_with(1)
        with mock.patch.object(planes, "_post", post_failing("/episode", 400)):
            self.assertEqual({"refused": 1}, gi.drain_once(spool, actor="t", writer=gi.Writer(), now=1))
        self.assertEqual({"quarantined": 1}, spool.counts())
        reason, = spool.conn.execute("SELECT reason FROM deliveries WHERE id='d0'").fetchone()
        self.assertIn("permanent refusal", reason)
        self.assertIn("HTTP 400", reason)

        for path in ("/graph/create", "/graph/label"):
            with self.subTest(path=path):
                spool = spool_with(3)
                with mock.patch.object(planes, "_post", post_failing(path, 400)):
                    counts = gi.drain_once(spool, actor="t", writer=gi.Writer(), now=1)
                self.assertEqual({"retry": 3}, counts)
                self.assertEqual({"pending": 3}, spool.counts())

        for code in (401, 403, 408, 429, 500, 503):
            with self.subTest(code=code):
                spool = spool_with(1)
                with mock.patch.object(planes, "_post", post_failing("/episode", code)):
                    self.assertEqual({"retry": 1}, gi.drain_once(spool, actor="t", writer=gi.Writer(), now=1))
                self.assertEqual({"pending": 1}, spool.counts())

        def transport(endpoint, body, **kwargs):
            if endpoint == "/episode":
                raise planes.PlaneError("/episode gave no answer (TimeoutError)") from TimeoutError()
            return {}
        spool = spool_with(1)
        with mock.patch.object(planes, "_post", transport):
            self.assertEqual({"retry": 1}, gi.drain_once(spool, actor="t", writer=gi.Writer(), now=1))

    def test_unwatched_repository_is_not_written(self):
        spool = gi.Spool(":memory:")
        spool.add("d1", "push", delivery("push")["body"])
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
        self.assertFalse(gi.verify_signature(SECRET, "sha256=\u00e9\u00e9", b"x"))  # non-ASCII: refused, no TypeError


class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.start()

    def start(self, **kw):
        self.spool = gi.Spool(":memory:")
        self.wake = threading.Event()
        self.metrics = gi.Metrics()
        handler = gi.make_handler(self.spool, SECRET, {"intigraph/camayoc"}, "/hooks/github", self.wake,
                                  metrics=self.metrics, **kw)
        self.server = gi.LimitedServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def restart(self, **kw):
        self.tearDown()
        self.start(**kw)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def post(self, rec: dict, *, body: bytes | None = None, secret: bytes | None = SECRET,
             path: str = "/hooks/github", length: str | None = None, method: str = "POST",
             extra: dict | None = None, drop: tuple = ()) -> tuple[int, dict]:
        body = body_bytes(rec) if body is None else body
        headers = dict(rec["headers"])
        if secret is not None:
            headers["X-Hub-Signature-256"] = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
        if length is not None:
            headers["Content-Length"] = length
        headers.update(extra or {})
        for name in drop:
            headers.pop(name, None)
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
        self.last_raw = raw
        out = (resp.status, json.loads(raw or b"{}"))
        conn.close()
        return out

    def test_recruitment_inbox_is_opt_in_and_only_after_signature_verification(self):
        rec = delivery("check_run.completed")
        self.assertEqual(202, self.post(rec)[0])
        self.assertEqual(0, self.spool.conn.execute("SELECT COUNT(*) FROM recruitment_events").fetchone()[0])
        self.restart(recruitment_inbox=True)
        self.assertEqual(401, self.post(rec, secret=b"wrong")[0])
        self.assertEqual(0, self.spool.conn.execute("SELECT COUNT(*) FROM recruitment_events").fetchone()[0])
        self.assertEqual(202, self.post(rec)[0])
        self.assertEqual(1, self.spool.conn.execute("SELECT COUNT(*) FROM recruitment_events").fetchone()[0])

    def test_accept_then_signed_replay_is_refused(self):
        rec = delivery("pull_request.closed")
        self.assertEqual((202, "accepted"), (lambda r: (r[0], r[1]["outcome"]))(self.post(rec)))
        self.assertEqual(409, self.post(rec)[0])  # same delivery id, valid signature: a replay
        self.assertEqual(b"", self.last_raw)
        self.assertTrue(self.wake.is_set())
        self.assertEqual({"pending": 1}, self.spool.counts())
        self.assertEqual(1, self.metrics.counts["replay"])

    def test_full_spool_answers_503(self):
        old = gi.MAX_PENDING
        gi.MAX_PENDING = 1
        try:
            rec = delivery("pull_request.closed")
            self.assertEqual(202, self.post(rec)[0])
            other = copy.deepcopy(delivery("push"))
            self.assertEqual(503, self.post(other)[0])
        finally:
            gi.MAX_PENDING = old

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

    # ---- aegis-xxoa.5 internet-facing hardening: every negative arm spools nothing

    def test_negative_arms_are_bare_and_spool_nothing(self):
        rec = delivery("pull_request.closed")
        arms = {
            "unsigned": (401, dict(secret=None)),
            "wrong signature": (401, dict(secret=b"wrong")),
            "sha1 only": (401, dict(secret=None, extra={"X-Hub-Signature": "sha1=00"})),
            "wrong path": (404, dict(path="/hooks/github/x")),
            "query string": (404, dict(path="/hooks/github?a=1")),
            "GET on the path": (405, dict(method="GET", body=b"")),
            "PUT on the path": (405, dict(method="PUT")),
            "GET elsewhere": (404, dict(method="GET", path="/healthz", body=b"")),
            "oversize declared": (413, dict(length=str(gi.MAX_BODY + 1))),
            "unknown event": (400, dict(extra={"X-GitHub-Event": "star"})),
            "malformed delivery id": (400, dict(extra={"X-GitHub-Delivery": "../../etc"})),
            "not json content type": (415, dict(extra={"Content-Type": "application/x-www-form-urlencoded"})),
        }
        for name, (code, kw) in arms.items():
            with self.subTest(name):
                status, _ = self.post(rec, **kw)
                self.assertEqual(code, status)
                self.assertEqual(b"", self.last_raw, "refusals carry no detail")
        self.assertEqual({}, self.spool.counts())
        self.assertNotIn("accepted", self.metrics.counts)

    def test_chunked_body_without_length_is_refused(self):
        import socket
        sock = socket.create_connection(("127.0.0.1", self.server.server_address[1]), timeout=5)
        sock.sendall(b"POST /hooks/github HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\n")
        reply = sock.recv(200)
        sock.close()
        self.assertTrue(b" 411 " in reply.split(b"\r\n")[0], reply)
        self.assertEqual({}, self.spool.counts())

    def test_signed_but_not_json_is_refused(self):
        rec = delivery("pull_request.closed")
        self.assertEqual(400, self.post(rec, body=b"not json")[0])
        self.assertEqual({}, self.spool.counts())

    def test_configured_body_cap(self):
        self.restart(max_body=1024)
        rec = delivery("pull_request.closed")
        self.assertGreater(len(body_bytes(rec)), 1024)
        self.assertEqual(413, self.post(rec)[0])
        self.assertEqual({}, self.spool.counts())

    def test_header_count_cap(self):
        rec = delivery("pull_request.closed")
        many = {f"X-Pad-{i}": "x" for i in range(gi.MAX_HEADERS)}
        self.assertEqual(431, self.post(rec, extra=many)[0])
        self.assertEqual({}, self.spool.counts())

    def test_rejections_are_rate_limited_before_the_body_is_read(self):
        clock = [0.0]
        self.restart(limiter=gi.RejectLimiter(burst=3, rate=1.0, clock=lambda: clock[0]))
        rec = delivery("pull_request.closed")
        self.assertEqual([401, 401, 401, 429, 429], [self.post(rec, secret=b"bad")[0] for _ in range(5)])
        self.assertEqual(429, self.post(rec)[0], "while spent, even a valid delivery waits (the poller backstops)")
        clock[0] += 2.0  # two tokens back
        self.assertEqual(202, self.post(rec)[0])
        self.assertEqual({"pending": 1}, self.spool.counts())
        self.assertEqual(3, self.metrics.counts["rate_limited"])

    def test_metrics_textfile(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "github_ingress.prom"
            self.restart()
            clock = [1600000000.0]
            self.metrics = gi.Metrics(path, clock=lambda: clock[0])
            self.metrics.record("started")
            self.assertIn("camayoc_github_ingress_last_accepted_timestamp_seconds 1600000000", path.read_text())
            clock[0] = 1700000000.0
            self.tearDown()
            handler = gi.make_handler(self.spool, SECRET, {"intigraph/camayoc"}, "/hooks/github", self.wake,
                                      metrics=self.metrics)
            self.server = gi.LimitedServer(("127.0.0.1", 0), handler)
            threading.Thread(target=self.server.serve_forever, daemon=True).start()
            rec = delivery("pull_request.closed")
            self.post(rec, secret=b"bad")
            self.post(rec)
            text = path.read_text()
            self.assertIn('camayoc_github_ingress_requests_total{verdict="signature"} 1', text)
            self.assertIn('camayoc_github_ingress_requests_total{verdict="accepted"} 1', text)
            self.assertIn("camayoc_github_ingress_last_accepted_timestamp_seconds 1700000000", text)

    def test_metrics_rewrite_a_precreated_file_in_a_readonly_directory(self):
        import os
        if os.geteuid() == 0:
            self.skipTest("root ignores directory permissions")
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "textfile"
            d.mkdir()
            path = d / "github_ingress.prom"
            path.write_text("old\n")
            d.chmod(0o555)  # like node_exporter's root-owned 0755 directory
            try:
                m = gi.Metrics(path, clock=lambda: 1700000000.0)
                m.record("accepted")
                text = path.read_text()
            finally:
                d.chmod(0o755)
            self.assertIn('camayoc_github_ingress_requests_total{verdict="accepted"} 1', text)
            self.assertNotIn("old", text)
            self.assertEqual(["github_ingress.prom"], sorted(p.name for p in d.iterdir()))

    # ---- aegis-xxoa.5 point 3: GitHub source ranges behind Tailscale Funnel

    def _allowlist(self, tmp, hooks=("192.30.252.0/22", "2a0a:a440::/29"), age=0.0, name="meta-hooks.json"):
        import time as _t
        path = Path(tmp) / name
        path.write_text(json.dumps({"fetched_at": _t.time() - age, "hooks": list(hooks)}))
        return gi.SourceAllowlist(path, max_age=3600)

    def test_source_allowlist_arms(self):
        funnel = {"Tailscale-Funnel-Request": "?1"}
        with tempfile.TemporaryDirectory() as tmp:
            self.restart(sources=self._allowlist(tmp))
            # GitHub address forwarded by Funnel: allowed through to the normal checks
            self.assertEqual(202, self.post(delivery("pull_request.closed"),
                                            extra={**funnel, "X-Forwarded-For": "192.30.252.10"})[0])
            self.assertEqual(202, self.post(delivery("push"),
                                            extra={**funnel, "X-Forwarded-For": "2a0a:a440::1"})[0])
            # non-GitHub source, validly signed: refused before the body is read
            self.assertEqual(403, self.post(delivery("release.published"),
                                            extra={**funnel, "X-Forwarded-For": "203.0.113.77"})[0])
            self.assertEqual(b"", self.last_raw)
            # no Funnel marker: no provable origin, even with a GitHub address
            self.assertEqual(403, self.post(delivery("release.published"),
                                            extra={"X-Forwarded-For": "192.30.252.10"})[0])
            # chained or garbage forwarded value: refused
            for xff in ("203.0.113.77, 192.30.252.10", "not-an-ip", ""):
                self.assertEqual(403, self.post(delivery("release.published"),
                                                extra={**funnel, "X-Forwarded-For": xff})[0], xff)
            self.assertEqual(403, self.post(delivery("release.published"), extra=funnel)[0])
            # a marker with any other value is not Funnel's
            self.assertEqual(403, self.post(delivery("release.published"),
                                            extra={"Tailscale-Funnel-Request": "1",
                                                   "X-Forwarded-For": "192.30.252.10"})[0])
            self.assertEqual({"pending": 2}, self.spool.counts())
            self.assertEqual(7, self.metrics.counts["source"])

    def test_source_allowlist_fails_closed(self):
        ok = {"Tailscale-Funnel-Request": "?1", "X-Forwarded-For": "192.30.252.10"}
        with tempfile.TemporaryDirectory() as tmp:
            for name, src in (("stale", self._allowlist(tmp, age=7200, name="stale.json")),
                              ("empty", self._allowlist(tmp, hooks=(), name="empty.json")),
                              ("missing", gi.SourceAllowlist(Path(tmp) / "absent.json")),
                              ("future", self._allowlist(tmp, age=-3600, name="future.json"))):
                self.restart(sources=src)
                self.assertEqual(403, self.post(delivery("pull_request.closed"), extra=ok)[0], name)
                self.assertEqual(1, self.metrics.counts["source_unknown"], name)
            bad = Path(tmp) / "bad.json"
            bad.write_text("{not json")
            self.restart(sources=gi.SourceAllowlist(bad))
            self.assertEqual(403, self.post(delivery("pull_request.closed"), extra=ok)[0])
            self.assertEqual({}, self.spool.counts())

    def test_source_allowlist_rereads_a_refreshed_file(self):
        import os as _os
        ok = {"Tailscale-Funnel-Request": "?1", "X-Forwarded-For": "198.51.100.5"}
        with tempfile.TemporaryDirectory() as tmp:
            src = self._allowlist(tmp)
            self.restart(sources=src)
            self.assertEqual(403, self.post(delivery("pull_request.closed"), extra=ok)[0])
            self._allowlist(tmp, hooks=("198.51.100.0/24",))
            _os.utime(src.path, (1, 2_000_000_000))  # a new mtime, as a timer refresh gives
            self.assertEqual(202, self.post(delivery("pull_request.closed"), extra=ok)[0])

    def test_direct_non_loopback_peer_is_judged_by_its_own_address(self):
        src = gi.SourceAllowlist(Path("/nonexistent"))
        self.assertEqual("203.0.113.9", src.client("203.0.113.9", {"X-Forwarded-For": "192.30.252.1"}))

    def test_meta_refresh_writes_only_a_valid_nonempty_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "meta-hooks.json"
            body = json.dumps({"hooks": ["192.30.252.0/22", "2a0a:a440::/29"], "web": ["1.2.3.0/24"]}).encode()
            self.assertEqual(0, gi.refresh_meta(out, fetch=lambda: body, clock=lambda: 1700000000.0))
            self.assertEqual({"fetched_at": 1700000000.0, "hooks": ["192.30.252.0/22", "2a0a:a440::/29"]},
                             json.loads(out.read_text()))
            before = out.read_bytes()
            for bad in (b'{"hooks": []}', b'{"hooks": ["192.30.252.1/22"]}', b'{"web": []}', b"<html>"):
                self.assertEqual(2, gi.refresh_meta(out, fetch=lambda b=bad: b), bad)
                self.assertEqual(before, out.read_bytes(), bad)

            def boom():
                raise OSError("network down")
            self.assertEqual(2, gi.refresh_meta(out, fetch=boom))
            self.assertEqual(before, out.read_bytes())

    def test_metrics_rewrites_in_place_when_directory_is_a_read_only_mount(self):
        """ProtectSystem=strict binds only the .prom file writable: creating the
        temp file fails with EROFS, not EACCES, and must take the same fallback."""
        import errno as _errno
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "github_ingress.prom"
            path.write_text("old\n")
            real = Path.write_bytes

            def ro_mount(self_path, data):
                if self_path.name.endswith(".tmp"):
                    raise OSError(_errno.EROFS, "Read-only file system")
                return real(self_path, data)

            with mock.patch.object(Path, "write_bytes", ro_mount), \
                    mock.patch("sys.stderr") as err:
                gi.Metrics(path, clock=lambda: 1700000000.0).record("accepted")
            self.assertIn('camayoc_github_ingress_requests_total{verdict="accepted"} 1', path.read_text())
            self.assertNotIn("old", path.read_text())
            self.assertEqual(["github_ingress.prom"], sorted(p.name for p in Path(tmp).iterdir()))
            err.write.assert_not_called()

    def test_metrics_other_write_errors_are_reported_not_hidden(self):
        import errno as _errno
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "github_ingress.prom"
            path.write_text("old\n")
            with mock.patch.object(Path, "write_bytes", side_effect=OSError(_errno.ENOSPC, "full")), \
                    mock.patch("sys.stderr") as err:
                gi.Metrics(path).record("accepted")  # never raises into the receiver
            self.assertEqual("old\n", path.read_text())
            err.write.assert_called_once_with("github-ingress metrics write failed: OSError\n")

    def test_concurrency_cap_closes_extra_connections(self):
        self.tearDown()
        self.spool, self.wake = gi.Spool(":memory:"), threading.Event()
        handler = gi.make_handler(self.spool, SECRET, {"intigraph/camayoc"}, "/hooks/github", self.wake)
        self.server = gi.LimitedServer(("127.0.0.1", 0), handler, max_concurrent=1)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        import socket
        slow = socket.create_connection(("127.0.0.1", self.server.server_address[1]))
        slow.sendall(b"POST /hooks/github HTTP/1.1\r\nHost: x\r\n")  # holds the one slot
        try:
            import time as _t
            _t.sleep(0.2)
            with self.assertRaises((ConnectionError, http.client.RemoteDisconnected, OSError)):
                self.post(delivery("pull_request.closed"))
        finally:
            slow.close()
        self.assertEqual({}, self.spool.counts())

    def test_trickling_clients_cannot_hold_every_slot(self):
        """wu's reproduction: clients that send one header byte just inside the
        per-read timeout held every slot. A whole-request deadline frees them."""
        import socket
        import time as _t
        self.tearDown()
        self.spool, self.wake, self.metrics = gi.Spool(":memory:"), threading.Event(), gi.Metrics()
        handler = gi.make_handler(self.spool, SECRET, {"intigraph/camayoc"}, "/hooks/github", self.wake,
                                  metrics=self.metrics, deadline=1.0)
        self.server = gi.LimitedServer(("127.0.0.1", 0), handler, max_concurrent=2)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        port = self.server.server_address[1]
        stop = threading.Event()

        def trickle():
            s = socket.create_connection(("127.0.0.1", port))
            try:
                s.sendall(b"POST /hooks/github HTTP/1.1\r\n")
                while not stop.is_set():
                    s.sendall(b"X")  # one byte, well inside the 10 s per-read timeout
                    _t.sleep(0.2)
            except OSError:
                pass  # the deadline closed us
            finally:
                s.close()

        trick = [threading.Thread(target=trickle, daemon=True) for _ in range(2)]
        for th in trick:
            th.start()
        _t.sleep(1.6)  # past the deadline: both slots must be free again
        try:
            self.assertEqual(202, self.post(delivery("pull_request.closed"))[0])
        finally:
            stop.set()
        self.assertEqual({"pending": 1}, self.spool.counts())
        self.assertGreaterEqual(self.metrics.counts.get("deadline", 0), 2)

    def test_slow_body_is_cut_at_the_deadline_and_spools_nothing(self):
        import socket
        import time as _t
        self.restart(deadline=0.5)
        rec = delivery("pull_request.closed")
        body = body_bytes(rec)
        sig = "sha256=" + hmac.new(SECRET, body, hashlib.sha256).hexdigest()
        head = (f"POST /hooks/github HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
                f"X-GitHub-Event: pull_request\r\nX-GitHub-Delivery: {rec['headers']['X-GitHub-Delivery']}\r\n"
                f"X-Hub-Signature-256: {sig}\r\nContent-Length: {len(body)}\r\n\r\n").encode()
        s = socket.create_connection(("127.0.0.1", self.server.server_address[1]), timeout=5)
        s.sendall(head + body[:10])
        _t.sleep(1.0)
        try:
            s.sendall(body[10:])
            reply = s.recv(100)
        except OSError:
            reply = b""
        s.close()
        self.assertEqual(b"", reply, "a request past its deadline gets no answer")
        self.assertEqual({}, self.spool.counts())
        self.assertEqual(1, self.metrics.counts["deadline"])

    def test_captured_body_with_a_fresh_delivery_id_is_refused(self):
        rec = delivery("pull_request.closed")
        self.assertEqual(202, self.post(rec)[0])
        fresh = copy.deepcopy(rec)
        fresh["headers"]["X-GitHub-Delivery"] = "11111111-2222-3333-4444-555555555555"
        self.assertEqual(409, self.post(fresh)[0])
        self.assertEqual(b"", self.last_raw)
        self.assertEqual({"pending": 1}, self.spool.counts())
        self.assertEqual(1, self.metrics.counts["replay_body"])

    def test_seen_bodies_is_bounded_and_expires(self):
        clock = [0.0]
        seen = gi.SeenBodies(limit=2, ttl=10, clock=lambda: clock[0])
        self.assertTrue(seen.check_and_add(b"a"))
        self.assertFalse(seen.check_and_add(b"a"))
        self.assertTrue(seen.check_and_add(b"b"))
        self.assertTrue(seen.check_and_add(b"c"))  # evicts the oldest past the limit on next call
        self.assertTrue(seen.check_and_add(b"d"))
        self.assertLessEqual(len(seen.seen), 3)
        clock[0] = 100.0
        self.assertTrue(seen.check_and_add(b"d"), "expired digests are forgotten")

    def test_verdict_log_has_no_body_or_header_values(self):
        import io
        from contextlib import redirect_stderr
        rec = delivery("pull_request.closed")
        buf = io.StringIO()
        with redirect_stderr(buf):
            self.post(rec, secret=b"bad")
            self.post(rec, extra={"X-GitHub-Delivery": "evil/../line"}, secret=None)
            self.post(rec)
        log = buf.getvalue()
        self.assertIn("verdict=signature status=401 event=- delivery=-", log)
        self.assertIn("verdict=accepted status=202 event=pull_request delivery=00000000-0000-0000-0000-00005eed0002", log)
        self.assertNotIn("sha256=", log)
        self.assertNotIn("full_name", log)
        self.assertNotIn("evil", log)


class SplitDrainTests(unittest.TestCase):
    def test_group_shared_spool_is_0660_and_never_world_readable(self):
        import os, stat
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "spool.db")
            gi.Spool(path, group_shared=True).add("d1", "push", delivery("push")["body"])
            for suffix in ("", "-wal", "-shm"):
                if os.path.exists(path + suffix):
                    self.assertEqual(0o660, stat.S_IMODE(os.stat(path + suffix).st_mode), suffix)

    def _foreign_spool(self, tmp: str, perms: int) -> str:
        """A spool the receiver created, as the drain (another uid) sees it."""
        import os
        path = str(Path(tmp) / "spool.db")
        gi.Spool(path, group_shared=True).add("d1", "push", delivery("push")["body"])
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(path + suffix):
                os.chmod(path + suffix, perms)
        return path

    def test_drain_opens_a_foreign_owned_0660_spool_without_chmod(self):
        # aegis-w9e: the drain (chaski) crash-looped on chmod EPERM of the
        # receiver-owned spool. A non-owner must verify, not chmod.
        import os
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            path = self._foreign_spool(tmp, 0o660)
            with mock.patch.object(gi.os, "geteuid", return_value=os.geteuid() + 1), \
                 mock.patch.object(gi.os, "chmod", side_effect=PermissionError("EPERM")) as chmod:
                spool = gi.Spool(path, group_shared=True)
                self.assertEqual(1, spool.pending())
            chmod.assert_not_called()

    def test_drain_refuses_a_foreign_owned_world_readable_spool(self):
        import os
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            path = self._foreign_spool(tmp, 0o666)
            with mock.patch.object(gi.os, "geteuid", return_value=os.geteuid() + 1):
                with self.assertRaisesRegex(PermissionError, "expected 0660"):
                    gi.Spool(path, group_shared=True)

    def test_drain_refuses_a_foreign_owned_spool_that_is_not_group_shared(self):
        import os
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            path = self._foreign_spool(tmp, 0o660)
            with mock.patch.object(gi.os, "geteuid", return_value=os.geteuid() + 1):
                with self.assertRaisesRegex(PermissionError, "owned by another user"):
                    gi.Spool(path)

    def test_spool_path_that_is_a_symlink_is_refused(self):
        import os
        with tempfile.TemporaryDirectory() as tmp:
            real = self._foreign_spool(tmp, 0o660)
            link = str(Path(tmp) / "link.db")
            os.symlink(real, link)
            with self.assertRaisesRegex(PermissionError, "not a regular file"):
                gi.Spool(link, group_shared=True)
            # A dangling link is refused BEFORE anything is created through it.
            target = Path(tmp) / "never-created.db"
            dangling = str(Path(tmp) / "dangling.db")
            os.symlink(target, dangling)
            with self.assertRaisesRegex(PermissionError, "not a regular file"):
                gi.Spool(dangling, group_shared=True)
            self.assertFalse(target.exists())

    def test_owner_still_tightens_a_loose_spool_to_0660(self):
        import os, stat
        with tempfile.TemporaryDirectory() as tmp:
            path = self._foreign_spool(tmp, 0o666)
            gi.Spool(path, group_shared=True)
            self.assertEqual(0o660, stat.S_IMODE(os.stat(path).st_mode))

    def test_no_drain_receiver_never_writes_and_drain_cli_does(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "spool.db")
            spool = gi.Spool(path)
            spool.add("00000000-0000-0000-0000-00005eed0007", "push", delivery("push")["body"])
            out = subprocess.run([sys.executable, str(ROOT / "scripts/github_ingress.py"), "drain",
                                  "--spool", path, "--actor", "tester", "--repo", "someone/else"],
                                 capture_output=True, text=True, timeout=60)
            self.assertEqual(0, out.returncode, out.stderr)
            self.assertEqual({"foreign": 1}, json.loads(out.stdout.strip().splitlines()[-1]))


class FakeTransport:
    """Serves the recorded poll fixtures; counts requests; ETag 304 on repeat."""

    def __init__(self, remaining: int = 4000):
        self.remaining = remaining
        self.calls: list[str] = []
        self.files = {
            "/repos/intigraph/camayoc": "repo.json",
            "/repos/intigraph/camayoc/events?per_page=100": "events-page.json",
            "/repos/intigraph/camayoc/actions/runs?per_page=20": "runs-page.json",
            "/repos/intigraph/camayoc/pulls?state=open&per_page=100": "pulls-open.json",
            "/repos/intigraph/camayoc/pulls/177": "pull-177.json",
            "/repos/intigraph/camayoc/pulls/172": "pull-172.json",
        }

    overrides: dict  # path -> list of (status, headers, body), served first, once each

    def get(self, path, etag=None):
        self.calls.append(path)
        queued = getattr(self, "overrides", {}).get(path)
        if queued:
            return queued.pop(0)
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
        # 2 PR, 2 push, 2 review, 2 comment events and 1 run; DeleteEvent is not mapped;
        # plus one open-list sighting (aegis-xxoa.5).
        self.assertEqual(10, first["accepted"])
        # Hydration: each object once per cycle, though two events name PR 177.
        self.assertEqual(1, t.calls.count("/repos/intigraph/camayoc/pulls/177"))
        self.assertIn("/repos/intigraph/camayoc/pulls/140", t.calls)  # recorded 404: stays trimmed
        self.assertEqual({"pending": 10}, p.spool.counts())
        second = p.cycle(now=1100)
        self.assertEqual(3, second["not_modified"])  # events, open list, runs
        self.assertNotIn("accepted", second)
        drained = gi.drain_once(p.spool, actor="t", writer=Collect(), repos={"intigraph/camayoc"}, now=1200)
        self.assertEqual(0, drained.get("retry", 0))
        self.assertGreaterEqual(drained["written"], 6)

    def test_429_ends_the_cycle_and_backs_off_until_retry_after(self):
        t = FakeTransport()
        t.overrides = {"/repos/intigraph/camayoc/events?per_page=100": [(429, {"retry-after": "120"}, b"")]}
        spool = gi.Spool(":memory:")
        receipt = self.poller(t, spool).cycle(now=1000)
        self.assertEqual(1120, receipt["rate_limited"])
        self.assertNotIn("/repos/intigraph/camayoc/actions/runs?per_page=20", t.calls)  # cycle ended at once
        calls = len(t.calls)
        self.assertIn("backoff", self.poller(t, spool).cycle(now=1100)["skipped"])
        self.assertEqual(calls, len(t.calls))  # not even /rate_limit during the backoff
        later = self.poller(t, spool).cycle(now=1121)
        self.assertNotIn("skipped", later)
        self.assertEqual(10, later["accepted"])  # 9 events + the open-list sighting of PR 177
        self.assertEqual(0, spool.get_state("backoff_n"))

    def test_403_with_spent_quota_waits_for_the_reset_and_otherwise_doubles(self):
        t = FakeTransport()
        t.overrides = {"/repos/intigraph/camayoc": [(403, {"x-ratelimit-remaining": "0",
                                                           "x-ratelimit-reset": "5000"}, b"")]}
        spool = gi.Spool(":memory:")
        self.assertEqual(5000, self.poller(t, spool).cycle(now=1000)["rate_limited"])
        t.overrides = {"/repos/intigraph/camayoc": [(403, {}, b""), (403, {}, b"")]}
        self.assertEqual(5000 + 120, self.poller(t, spool).cycle(now=5000)["rate_limited"])  # 60 * 2**1
        self.assertEqual(5120 + 240, self.poller(t, spool).cycle(now=5120)["rate_limited"])

    def test_transient_hydration_failure_stays_retryable(self):
        t = FakeTransport()
        t.overrides = {"/repos/intigraph/camayoc/pulls/177": [(502, {}, b"")]}
        spool = gi.Spool(":memory:")
        p = self.poller(t, spool)
        receipt = p.cycle(now=1000)
        self.assertEqual(2, receipt["hydration_retry"])  # both PR 177 events
        rows = spool.conn.execute("SELECT state, attempts, reason FROM deliveries WHERE state='hydrate'").fetchall()
        self.assertEqual(2, len(rows))
        self.assertTrue(all(r[1] == 1 and "HTTP 502" in r[2] for r in rows), rows)
        gi.drain_once(spool, actor="t", writer=Collect(), now=1000)  # drain never touches 'hydrate'
        self.assertEqual(2, spool.conn.execute("SELECT COUNT(*) FROM deliveries WHERE state='hydrate'").fetchone()[0])
        self.assertEqual(0, spool.conn.execute(
            "SELECT COUNT(*) FROM deliveries WHERE state='quarantined' AND reason LIKE '%PR_%'").fetchone()[0])
        p.cycle(now=1100)  # past the 10 s backoff: fetch succeeds
        self.assertEqual(0, spool.conn.execute("SELECT COUNT(*) FROM deliveries WHERE state='hydrate'").fetchone()[0])
        writer = Collect()
        gi.drain_once(spool, actor="t", writer=writer, now=1200)
        kinds = {activity(b)["githubEventKind"] for b in writer.bodies}
        self.assertTrue({"pull_request.opened", "pull_request.closed"} <= kinds, kinds)

    def test_spent_budget_leaves_hydration_pending_not_quarantined(self):
        spool = gi.Spool(":memory:")
        # rate_limit is free; repo, events, runs = 3; one hydration fits, the rest wait.
        receipt = self.poller(FakeTransport(), spool, budget_per_hour=4).cycle(now=10)
        self.assertTrue(receipt["over_budget"])
        waiting = spool.conn.execute("SELECT COUNT(*) FROM deliveries WHERE state='hydrate'").fetchone()[0]
        self.assertGreaterEqual(waiting, 1)
        gi.drain_once(spool, actor="t", writer=Collect(), now=11)
        self.assertEqual(waiting, spool.conn.execute(
            "SELECT COUNT(*) FROM deliveries WHERE state='hydrate'").fetchone()[0])
        self.poller(FakeTransport(), spool, budget_per_hour=50).cycle(now=3600 * 2)  # next hour
        self.assertEqual(0, spool.conn.execute("SELECT COUNT(*) FROM deliveries WHERE state='hydrate'").fetchone()[0])

    def test_webhook_and_poll_of_one_change_write_once(self):
        spool = gi.Spool(":memory:")
        rec = delivery("pull_request.closed")
        spool.add(rec["headers"]["X-GitHub-Delivery"], "pull_request", rec["body"], now=0)
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

    def test_only_a_real_404_is_quarantined(self):
        spool = gi.Spool(":memory:")
        self.poller(FakeTransport(), spool).cycle(now=1)
        gi.drain_once(spool, actor="t", writer=Collect(), now=2)
        rows = spool.conn.execute("SELECT id, reason FROM deliveries WHERE state='quarantined'").fetchall()
        self.assertEqual(1, len(rows), rows)  # the review on PR 140, whose fetch was a recorded 404
        self.assertIn("pull_request", rows[0][1])


OPEN_LIST = "/repos/intigraph/camayoc/pulls?state=open&per_page=100"


def observed(writer: "Collect") -> list[dict]:
    return [activity(b) for b in writer.bodies if activity(b)["githubEventKind"] == "pull_request.observed"]


class OpenPullRequestSweepTests(unittest.TestCase):
    """aegis-xxoa.5: a quiet open PR is recorded, and a vanished one is resolved."""

    def poller(self, transport, spool):
        return gi.Poller(spool, transport, ["intigraph/camayoc"])

    def quiet(self) -> FakeTransport:
        """Only the open list carries PR 177: no events, no runs."""
        t = FakeTransport()
        empty = (200, {}, b"[]")
        t.overrides = {"/repos/intigraph/camayoc/events?per_page=100": [empty, empty],
                       "/repos/intigraph/camayoc/actions/runs?per_page=20": [(200, {}, b'{"workflow_runs": []}')] * 2}
        return t

    def test_a_quiet_open_pr_is_recorded_once(self):
        spool, t = gi.Spool(":memory:"), self.quiet()
        p = self.poller(t, spool)
        p.cycle(now=1)
        writer = Collect()
        gi.drain_once(spool, actor="t", writer=writer, now=2)
        seen = observed(writer)
        self.assertEqual(1, len(seen))
        self.assertEqual(("open", False), (seen[0]["githubState"], seen[0]["githubMerged"]))
        self.assertEqual("2026-10-27T18:50:00Z", seen[0]["occurredAt"])
        self.assertNotIn("githubActorLogin", seen[0])  # a sighting has no actor
        self.assertEqual(1, len(spool.open_prs("intigraph/camayoc")))
        # The list unchanged (304), then re-served without an ETag: no new record either way.
        p.cycle(now=100)
        t.overrides[OPEN_LIST] = [(200, {}, (POLL / "pulls-open.json").read_bytes())]
        p.cycle(now=200)
        again = Collect()
        gi.drain_once(spool, actor="t", writer=again, now=201)
        self.assertEqual([], observed(again))

    def test_a_pr_gone_from_the_list_is_fetched_and_recorded_as_it_is(self):
        spool, t = gi.Spool(":memory:"), self.quiet()
        p = self.poller(t, spool)
        p.cycle(now=1)
        gi.drain_once(spool, actor="t", writer=Collect(), now=2)
        t.overrides[OPEN_LIST] = [(200, {}, b"[]")]
        p.cycle(now=100)
        self.assertEqual(1, t.calls.count("/repos/intigraph/camayoc/pulls/177"))
        writer = Collect()
        gi.drain_once(spool, actor="t", writer=writer, now=101)
        seen = observed(writer)
        self.assertEqual(1, len(seen))
        self.assertEqual(("closed", True), (seen[0]["githubState"], seen[0]["githubMerged"]))
        self.assertEqual("24091efeed09250206b3f3295bc7a31867c5b2c2", seen[0]["githubMergeCommitSha"])
        self.assertEqual({}, spool.open_prs("intigraph/camayoc"))

    def test_a_full_page_never_presumes_a_pr_closed(self):
        spool, t = gi.Spool(":memory:"), self.quiet()
        p = self.poller(t, spool)
        p.cycle(now=1)
        other = json.loads((POLL / "pulls-open.json").read_text())
        other[0].update(node_id="PR_other", number=999, url="https://api.github.com/repos/intigraph/camayoc/pulls/999")
        t.overrides[OPEN_LIST.replace("per_page=100", "per_page=1")] = [(200, {}, json.dumps(other).encode())]
        old = gi.OPEN_PAGE
        gi.OPEN_PAGE = 1
        try:
            receipt = p.cycle(now=100)
        finally:
            gi.OPEN_PAGE = old
        self.assertEqual(["intigraph/camayoc"], receipt["truncated"])
        self.assertNotIn("/repos/intigraph/camayoc/pulls/177", t.calls)  # no gone-fetch
        self.assertIn("PR_kwDOTvaTnM8AAAABLF8ZkA", spool.open_prs("intigraph/camayoc"))  # memory kept

    def test_a_failed_list_changes_nothing(self):
        spool, t = gi.Spool(":memory:"), self.quiet()
        p = self.poller(t, spool)
        p.cycle(now=1)
        t.overrides[OPEN_LIST] = [(502, {}, b"bad gateway")]
        receipt = p.cycle(now=100)
        self.assertTrue(any("pulls?state=open" in e for e in receipt["errors"]))
        self.assertNotIn("/repos/intigraph/camayoc/pulls/177", t.calls)
        self.assertEqual(1, len(spool.open_prs("intigraph/camayoc")))


class MergeabilityObservationTests(unittest.TestCase):
    quiet = OpenPullRequestSweepTests.quiet

    def poller(self, transport, spool):
        return gi.Poller(spool, transport, ["intigraph/camayoc"], observe_mergeability=True)

    def details(self, transport, **fields):
        pr = json.loads((POLL / "pulls-open.json").read_text())[0]
        pr.update(mergeable=False, mergeable_state="dirty", **fields)
        pr["base"]["sha"] = "a" * 40
        path = "/repos/intigraph/camayoc/pulls/177"
        transport.overrides[path] = [(200, {}, json.dumps(pr).encode())]
        return pr

    def test_detail_snapshot_survives_redaction_and_has_observation_time(self):
        spool, t = gi.Spool(":memory:"), self.quiet()
        self.details(t)
        self.poller(t, spool).cycle(now=1000)
        writer = Collect()
        gi.drain_once(spool, actor="t", writer=writer, now=1001)
        seen = observed(writer)
        self.assertEqual(1, len(seen))
        self.assertIs(False, seen[0]["githubMergeable"])
        self.assertEqual("dirty", seen[0]["githubMergeableState"])
        self.assertEqual("a" * 40, seen[0]["githubBaseSha"])
        self.assertEqual("1970-01-01T00:16:40Z", seen[0]["occurredAt"])

    def test_unchanged_list_rechecks_detail_and_does_not_reemit_unchanged_snapshot(self):
        spool, t = gi.Spool(":memory:"), self.quiet()
        p = self.poller(t, spool)
        self.details(t)
        p.cycle(now=1000)
        gi.drain_once(spool, actor="t", writer=Collect(), now=1001)
        self.details(t)
        p.cycle(now=1100)
        writer = Collect()
        gi.drain_once(spool, actor="t", writer=writer, now=1101)
        self.assertEqual([], observed(writer))
        pr = self.details(t)
        pr["base"]["sha"] = "b" * 40
        pr["mergeable"], pr["mergeable_state"] = True, "clean"
        t.overrides["/repos/intigraph/camayoc/pulls/177"] = [(200, {}, json.dumps(pr).encode())]
        p.cycle(now=1200)
        gi.drain_once(spool, actor="t", writer=writer, now=1201)
        self.assertEqual(1, len(observed(writer)))
        self.assertEqual("b" * 40, observed(writer)[0]["githubBaseSha"])
        self.assertIs(True, observed(writer)[0]["githubMergeable"])

    def test_detail_unknown_does_not_become_clean(self):
        spool, t = gi.Spool(":memory:"), self.quiet()
        pr = self.details(t)
        pr["mergeable"], pr["mergeable_state"] = None, "unknown"
        t.overrides["/repos/intigraph/camayoc/pulls/177"] = [(200, {}, json.dumps(pr).encode())]
        self.poller(t, spool).cycle(now=1000)
        writer = Collect()
        gi.drain_once(spool, actor="t", writer=writer, now=1001)
        self.assertNotIn("githubMergeable", observed(writer)[0])
        self.assertEqual("unknown", observed(writer)[0]["githubMergeableState"])

    def test_returning_state_is_a_new_observation_across_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'spool.db')
            writer = Collect()
            for index, mergeable in enumerate((False, True, False, False)):
                spool, t = gi.Spool(path), self.quiet()
                pr = self.details(t)
                pr['mergeable'] = mergeable
                pr['mergeable_state'] = 'clean' if mergeable else 'dirty'
                t.overrides['/repos/intigraph/camayoc/pulls/177'] = [(200, {}, json.dumps(pr).encode())]
                self.poller(t, spool).cycle(now=1000 + index * 100)
                gi.drain_once(spool, actor='t', writer=writer, now=1001 + index * 100)
                spool.conn.close()
            states = observed(writer)
            self.assertEqual([False, True, False], [p['githubMergeable'] for p in states])
            self.assertEqual('1970-01-01T00:20:00Z', states[-1]['occurredAt'])
            self.assertEqual(3, len({p['githubActivityKey'] for p in states}))

    def test_pending_observation_retries_byte_identically_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'spool.db')
            spool, t = gi.Spool(path), self.quiet()
            self.details(t)
            self.poller(t, spool).cycle(now=1000)
            attempts = []
            def fail(body):
                attempts.append(body)
                raise OSError('fixture unavailable')
            gi.drain_once(spool, actor='t', writer=fail, now=1001)
            spool.conn.close()
            spool, t = gi.Spool(path), self.quiet()
            self.details(t)
            self.poller(t, spool).cycle(now=1100)
            self.assertEqual(1, spool.pending())
            writer = Collect()
            gi.drain_once(spool, actor='t', writer=writer, now=1101)
            self.assertEqual(attempts, writer.bodies)
            spool.conn.close()

    def test_cursor_failure_rolls_back_queued_observation(self):
        spool = gi.Spool(':memory:')
        payload = delivery('pull_request.opened')['body']
        spool.conn.executescript("CREATE TRIGGER reject_cursor BEFORE INSERT ON pr_observations "
                                 "BEGIN SELECT RAISE(ABORT, 'fixture failure'); END;")
        with self.assertRaises(sqlite3.IntegrityError):
            spool.observe_pr('example/project', 'PR_fixture', 'a' * 64, payload, 1000)
        self.assertEqual(0, spool.pending())
        self.assertEqual(0, spool.conn.execute('SELECT COUNT(*) FROM pr_observations').fetchone()[0])
        spool.conn.execute('DROP TRIGGER reject_cursor')
        self.assertEqual('accepted', spool.observe_pr('example/project', 'PR_fixture', 'a' * 64, payload, 1000))
        self.assertEqual('duplicate', spool.observe_pr('example/project', 'PR_fixture', 'a' * 64, payload, 1100))
        self.assertEqual(1, spool.pending())

    def test_detail_identity_mismatch_writes_no_sighting(self):
        spool, t = gi.Spool(":memory:"), self.quiet()
        pr = self.details(t)
        pr["node_id"] = "PR_other"
        t.overrides["/repos/intigraph/camayoc/pulls/177"] = [(200, {}, json.dumps(pr).encode())]
        receipt = self.poller(t, spool).cycle(now=1000)
        self.assertTrue(any("identity unknown" in x for x in receipt["errors"]))
        writer = Collect()
        gi.drain_once(spool, actor="t", writer=writer, now=1001)
        self.assertEqual([], observed(writer))

    def test_detail_uses_existing_budget_and_rate_backoff(self):
        spool, t = gi.Spool(":memory:"), self.quiet()
        p = gi.Poller(spool, t, ["intigraph/camayoc"], budget_per_hour=3, observe_mergeability=True)
        receipt = p.cycle(now=1000)
        self.assertTrue(receipt["over_budget"])
        self.assertNotIn("/repos/intigraph/camayoc/pulls/177", t.calls)
        self.assertEqual(3, receipt["requests"])
        spool, t = gi.Spool(":memory:"), self.quiet()
        t.overrides["/repos/intigraph/camayoc/pulls/177"] = [(429, {"retry-after": "120"}, b"")]
        p = self.poller(t, spool)
        self.assertEqual(1120, p.cycle(now=1000)["rate_limited"])
        calls = list(t.calls)
        p.cycle(now=1100)
        self.assertEqual(calls, t.calls)

    def test_duplicate_or_malformed_list_never_replaces_last_complete_roster(self):
        for malformed in ('duplicate', 'malformed'):
            with self.subTest(malformed=malformed):
                spool, t = gi.Spool(":memory:"), self.quiet()
                old = {'PR_retained': ('https://api.github.com/repos/intigraph/camayoc/pulls/999', 999, 'old')}
                spool.set_open_prs('intigraph/camayoc', old)
                pr = self.details(t)
                page = [pr, pr if malformed == 'duplicate' else None]
                t.overrides['/repos/intigraph/camayoc/pulls?state=open&per_page=100'] = [
                    (200, {}, json.dumps(page).encode())]
                receipt = self.poller(t, spool).cycle(now=1000)
                self.assertIn('intigraph/camayoc', receipt['truncated'])
                self.assertEqual(old, spool.open_prs('intigraph/camayoc'))


class SpoolPrivacyTests(unittest.TestCase):
    def test_spool_files_are_owner_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "spool.db")
            spool = gi.Spool(path)
            spool.add("d1", "push", delivery("push")["body"])
            for suffix in ("", "-wal", "-shm"):
                if Path(path + suffix).exists():
                    self.assertEqual(0o600, Path(path + suffix).stat().st_mode & 0o777, suffix)
            self.assertTrue(Path(path).exists())

    def test_bodies_are_stripped_before_they_touch_disk(self):
        push = copy.deepcopy(delivery("push")["body"])
        push["pusher"] = {"name": "scbrown", "email": "someone@example.invalid"}
        push["commits"] = [{"id": "1" * 40, "message": "secret plans",
                            "author": {"email": "someone@example.invalid"}}]
        push["head_commit"] = push["commits"][0]
        comment = copy.deepcopy(delivery("issue_comment.created")["body"])
        run = copy.deepcopy(delivery("workflow_run.completed")["body"])
        run["workflow_run"]["head_commit"] = {"author": {"email": "someone@example.invalid"}, "message": "m"}
        spool = gi.Spool(":memory:")
        spool.add("p", "push", push)
        spool.add("c", "issue_comment", comment)
        spool.add("w", "workflow_run", run)
        stored = " ".join(r[0].decode() for r in spool.conn.execute("SELECT body FROM deliveries"))
        for leaked in ("@example.invalid", "secret plans", "commits", "head_commit", "pusher", "Landed",
                       "Thanks", "Tracking"):
            self.assertNotIn(leaked, stored)
        self.assertIn("@donna-box", stored)  # the mention survives as itself
        # and the stripped spool still maps to the same records as the raw payloads
        writer = Collect()
        gi.drain_once(spool, actor="t", writer=writer)
        raw = [gi.episode_for(e, p, actor="t")[0] for e, p in
               (("push", push), ("issue_comment", comment), ("workflow_run", run))]
        # The drain asserts the (one, unchanged) repository node once (aegis-xxoa.5.1);
        # every other byte of each body is what the raw payload maps to.
        def no_repo(b):
            return json.dumps(dict(b, nodes=[n for n in b["nodes"] if n["type"] != "GitHubRepository"]),
                              sort_keys=True)
        self.assertEqual(sorted(no_repo(b) for b in raw), sorted(no_repo(b) for b in writer.bodies))
        self.assertEqual(1, sum(n["type"] == "GitHubRepository" for b in writer.bodies for n in b["nodes"]))

    def test_retention_is_short(self):
        self.assertLessEqual(gi.RETAIN_DAYS, 2)
        spool = gi.Spool(":memory:")
        spool.add("d1", "push", delivery("push")["body"], now=0)
        gi.drain_once(spool, actor="t", writer=Collect(), now=1)
        self.assertEqual(0, spool.purge(gi.RETAIN_DAYS, now=gi.RETAIN_DAYS * 86400 - 10))
        self.assertEqual(1, spool.purge(gi.RETAIN_DAYS, now=gi.RETAIN_DAYS * 86400 + 10))
        self.assertTrue(spool.activity_written(gi.episode_for("push", delivery("push")["body"], actor="t")[1]))


@requires_quipu
class QuipuRoundTripTests(unittest.TestCase):
    """The replayed episodes in a real store: shapes hold, re-ingest is a no-op,
    and the stored queries answer."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = str(Path(cls.tmp.name) / "s.db")
        subprocess.run([str(QUIPU), "graph", "create", GRAPH, "--db", cls.db], check=True, capture_output=True)
        _, cls.bodies = replay()
        cls.outputs = []
        for i, body in enumerate(cls.bodies + cls.bodies[:1]):
            path = Path(cls.tmp.name) / f"ep{i}.json"
            path.write_text(json.dumps(body))
            out = subprocess.run([str(QUIPU), "episode", str(path), "--db", cls.db,
                                  "--timestamp", "2026-10-28T00:00:00Z",
                                  "--base-ns", "http://aegis.gastown.local/ontology/"],
                                 capture_output=True, text=True, check=True)
            cls.outputs.append(out.stdout)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def read(self, sparql: str) -> str:
        return subprocess.run([str(QUIPU), "read", sparql, "--db", self.db], capture_output=True, text=True,
                              check=True).stdout

    def test_exported_window_conforms_to_the_shapes(self):
        ttl = subprocess.run([str(QUIPU), "export", "--graph", GRAPH, "--format", "turtle", "--db", self.db],
                             capture_output=True, text=True, check=True).stdout
        data = Path(self.tmp.name) / "window.ttl"
        data.write_text(ttl)
        out = subprocess.run([str(QUIPU), "validate", "--shapes", str(ROOT / "shapes/github.shapes.ttl"),
                              "--data", str(data)], capture_output=True, text=True)
        self.assertTrue(out.stdout.lstrip().startswith("valid"), out.stdout + out.stderr)
        self.assertEqual(8, ttl.count("aegis:GitHubActivity") or ttl.count("GitHubActivity>"))

    def test_repository_node_has_one_provenance_row_across_deliveries(self):
        """The item's probe, in a real store: per repo node, rows do not grow
        with deliveries. Before aegis-xxoa.5.1 each of the replayed deliveries
        added its own prov:wasGeneratedBy (and the 7 values beside it)."""
        out = self.read("SELECT ?e WHERE { GRAPH ?g { <http://aegis.gastown.local/ontology/github.R_kgDOTvaTnA> "
                        "<http://www.w3.org/ns/prov#wasGeneratedBy> ?e } }")
        self.assertIn("1 results", out)

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
