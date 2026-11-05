"""Quarantine plane routing — camayoc-s0h.

The bead's whole point is that routing which LOOKS implemented and silently
lands in ROOT is worse than no routing, because it is unfalsifiable from
outside. So these tests are mostly about refusals: what the router must decline
to do rather than what it does on the happy path.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


planes = load("planes")


class PostFailureTests(unittest.TestCase):
    """A response that never arrives is INDETERMINATE, never a crash and never
    an absence (aegis-4c3ppi chunk 02: a /knot read timed out at the client
    while all 100 triples landed)."""

    def _post_raising(self, exc):
        from unittest import mock
        with mock.patch.object(planes, "AUTH", "isolated-test-fixture"), mock.patch.object(planes.quipu_auth, "_disabled", set()), mock.patch.object(planes.urllib.request, "urlopen", side_effect=exc):
            return planes._post("/knot", {"turtle": ""}, client="test")

    def test_a_read_timeout_is_a_plane_error_saying_it_may_have_applied(self):
        with self.assertRaises(planes.PlaneError) as c:
            self._post_raising(TimeoutError("The read operation timed out"))
        self.assertIn("may have been applied", str(c.exception))
        self.assertIn("verify by reading back", str(c.exception))

    def test_a_connection_reset_is_a_plane_error_too(self):
        with self.assertRaises(planes.PlaneError):
            self._post_raising(ConnectionResetError(104, "Connection reset by peer"))

    def test_an_unreachable_store_keeps_its_own_message(self):
        import urllib.error
        with self.assertRaises(planes.PlaneError) as c:
            self._post_raising(urllib.error.URLError("Name or service not known"))
        self.assertIn("unreachable", str(c.exception))


class CompletionBindingTests(unittest.TestCase):
    def test_real_transport_preserves_write_identity_and_body(self):
        import io
        from unittest import mock
        seen = []
        def receive(request, **_kwargs):
            seen.append(request)
            return io.BytesIO(b'{"tx_id": 1}')
        body = {'graph': 'urn:test:isolated', 'turtle': ''}
        with mock.patch.object(planes, 'AUTH', 'isolated-test-fixture'), \
             mock.patch.object(planes.quipu_auth, '_disabled', set()), \
             mock.patch.object(planes.urllib.request, 'urlopen', side_effect=receive):
            planes._post('/knot', body, client='test', write_id='a'*64)
        self.assertEqual(seen[0].get_header('X-quipu-write-id'), 'a'*64)
        self.assertEqual(json.loads(seen[0].data), body)
        self.assertTrue(seen[0].get_header('Authorization'))

    def test_malformed_or_wrong_route_binding_refuses_before_http(self):
        from unittest import mock
        with mock.patch.object(planes.urllib.request, 'urlopen') as send:
            for route, write_id in (('/query', 'a'*64), ('/knot', ''),
                                    ('/knot', 'a'*63), ('/knot', 'A'*64),
                                    ('/knot', 5), ('/knot', 'a'*64+'\n')):
                with self.subTest(route=route, write_id=write_id):
                    with self.assertRaises(planes.PlaneError):
                        planes._post(route, {}, client='test', write_id=write_id)
            send.assert_not_called()


class RoutingTests(unittest.TestCase):
    """The routing table, which is pure and is where a mistake would be silent."""

    def test_each_source_kind_routes_to_its_own_plane(self):
        observed = planes.plane_for("observed")
        declared = planes.plane_for("declared")
        inferred = planes.plane_for("inferred")
        self.assertEqual(3, len({observed, declared, inferred}), "planes must be distinct")

    def test_inferred_never_shares_a_plane_with_observed(self):
        """The single thing this mechanism exists to prevent. If these ever
        collide, model-written facts sit exactly where parser-produced ones do
        and the sourceKind tag is decoration."""
        self.assertNotEqual(planes.plane_for("inferred"), planes.plane_for("observed"))
        self.assertNotEqual(planes.plane_for("inferred"), planes.plane_for("declared"))

    def test_an_unknown_source_kind_is_refused_not_defaulted(self):
        """Defaulting to ROOT is the failure mode. An unroutable write must
        stop, loudly."""
        with self.assertRaises(planes.PlaneError) as ctx:
            planes.plane_for("vibes")
        self.assertIn("Refusing to default to ROOT", str(ctx.exception))

    def test_the_routing_table_is_derived_from_the_plane_definitions(self):
        """Two hand-maintained copies would drift, and the drift would be
        invisible: a plane defined but unrouted looks exactly like one nobody
        writes to."""
        for kind, name in planes.ROUTES.items():
            self.assertIn(kind, planes.PLANES[name]["source_kinds"])

    def test_inferred_ranks_strictly_below_every_other_plane(self):
        """`low, promotable` in the ingress table. If inferred ever ranked
        level with records, a query-time trust floor could not exclude it."""
        inferred = planes.PLANES["crew:inferred"]["rank"]
        for name, spec in planes.PLANES.items():
            if name != "crew:inferred":
                self.assertLess(inferred, spec["rank"], f"{name} must outrank inferred")

    def test_every_plane_declares_a_trust_rank_in_one_chain(self):
        """A rank is comparable only within the chain that declares it, which
        is why quipu's label API demands both."""
        for spec in planes.PLANES.values():
            self.assertIsInstance(spec["rank"], int)
            self.assertTrue(spec["trust_iri"])

    def test_namespaces_are_parameters_not_hardcoded_hosts(self):
        """camayoc's own convention (CLAUDE.md)."""
        for spec in planes.PLANES.values():
            self.assertTrue(spec["iri"].startswith(planes.PLANE_NS))


class EpisodeBodyTests(unittest.TestCase):
    def test_the_body_targets_the_plane_the_source_kind_earns(self):
        body = planes.episode_body(
            "session-x", "@prefix ex: <http://ex/> .", "inferred", "claude", "session"
        )
        self.assertEqual(planes.plane_for("inferred"), body["graph"])

    def test_an_unroutable_write_raises_before_a_body_exists(self):
        """No body is produced for a sourceKind with no plane — a caller cannot
        accidentally POST one with the graph key missing, which /knot would
        have silently dropped anyway."""
        with self.assertRaises(planes.PlaneError):
            planes.episode_body("x", "", "guesswork", "claude", "session")


class StubQuipu(BaseHTTPRequestHandler):
    """A quipu with the plane routes. `missing` makes it an OLD quipu."""

    missing = False
    seen: list = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        type(self).seen.append((self.path, body))

        if self.missing:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        payload = (
            {"g": 7, "created": True}
            if self.path == "/graph/create"
            else {"tx_id": 42}
        )
        raw = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *_a):
        return


class EnsurePlanesTests(unittest.TestCase):
    def setUp(self):
        from unittest import mock
        self.credential = mock.patch.object(planes, "AUTH", "isolated-test-fixture")
        self.credential.start()
        self.addCleanup(self.credential.stop)
        self.state = mock.patch.object(planes.quipu_auth, "_disabled", set())
        self.state.start()
        self.addCleanup(self.state.stop)

    def serve(self, **behaviour):
        handler = type("H", (StubQuipu,), {"seen": [], **behaviour})
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        planes.SERVER = f"http://127.0.0.1:{server.server_port}"
        return handler

    def test_every_plane_is_both_registered_and_labelled(self):
        """Both or neither. A registered-but-unlabelled plane is read at equal
        trust by every query, which is the appearance of quarantine without the
        substance."""
        handler = self.serve()
        results = planes.ensure_planes("2026-01-01T00:00:00Z")
        self.assertEqual(len(planes.PLANES), len(results))
        paths = [p for p, _ in handler.seen]
        self.assertEqual(len(planes.PLANES), paths.count("/graph/create"))
        self.assertEqual(len(planes.PLANES), paths.count("/graph/label"))

    def test_every_label_carries_its_chain_with_its_rank(self):
        handler = self.serve()
        planes.ensure_planes("2026-01-01T00:00:00Z")
        labels = [b for p, b in handler.seen if p == "/graph/label"]
        self.assertTrue(labels)
        for body in labels:
            self.assertIn("chain", body["trust"])
            self.assertIn("rank", body["trust"])

    def test_an_old_quipu_without_the_routes_fails_loudly(self):
        """The regression that matters. Against a quipu with no plane routing,
        this must NOT quietly carry on writing to ROOT — that is exactly the
        'looks implemented, does nothing' state the bead describes."""
        self.serve(missing=True)
        with self.assertRaises(planes.PlaneError) as ctx:
            planes.ensure_planes("2026-01-01T00:00:00Z")
        self.assertIn("no plane routing", str(ctx.exception))

    def test_an_unreachable_store_is_not_evidence_about_the_planes(self):
        """'Could not look' is not 'the plane is absent' — the distinction the
        gate probe had to learn the hard way."""
        planes.SERVER = "http://127.0.0.1:1"
        with self.assertRaises(planes.PlaneError) as ctx:
            planes.ensure_planes("2026-01-01T00:00:00Z")
        self.assertIn("not evidence", str(ctx.exception))


if __name__ == "__main__":
    sys.exit(unittest.main())
