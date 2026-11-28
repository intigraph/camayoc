"""Explicit entity registration, duplicate refusals and fail-closed lookups."""
import contextlib
import io
import unittest
from unittest import mock

from camayoc_cli import cli, memory, planes, quipu


class EntityAddTests(unittest.TestCase):
    def client(self, rows=()):
        client = mock.Mock(spec=quipu.Client)
        client.query.side_effect = [[], list(rows)]
        return client

    def test_register_each_type_in_configured_namespace(self):
        with mock.patch.dict("os.environ", {"CAMAYOC_PLANE_NS": "https://example.org/planes/"}):
            for kind in ("Person", "System", "Agent"):
                client = self.client()
                out = memory.add_entity("new-name", kind, client=client, env={"SHANTY_AGENT": "tester"})
                self.assertEqual(0, out.code)
                turtle, graph, actor, source = client.knot.call_args.args
                self.assertIn(f"<{memory.ns()}{kind}>", turtle)
                self.assertIn(f"<{memory.ns()}new-name>", turtle)
                self.assertEqual(planes.plane_for("declared"), graph)
                self.assertEqual("tester", actor)
                self.assertEqual("camayoc:cy entity add", source)

    def test_duplicate_and_near_names_refuse_without_write(self):
        for name, label in (("Ellie", "Ellie"), ("ellie", "ELLIE"), ("elli", "Ellie"),
                            ("elllie", "Ellie"), ("elliee", "Ellie"), ("el-lie", "Ellie"),
                            ("tom", "Tim")):
            with self.subTest(name=name):
                client = self.client([{"iri": "urn:example:person", "label": label}])
                out = memory.add_entity(name, "Person", client=client)
                self.assertEqual(2, out.code)
                self.assertIn("urn:example:person", out.lines[0])
                client.knot.assert_not_called()
        client = self.client()
        client.query.side_effect = [[{"p": "label"}]]
        self.assertEqual(2, memory.add_entity("existing", "System", client=client).code)
        client.knot.assert_not_called()

    def test_local_name_and_alias_match(self):
        for row in ({"iri": "https://example.org/ellie"},
                    {"iri": "https://example.org/person-123", "label": {"value": "Ellie"}}):
            client = self.client([row])
            self.assertEqual(2, memory.add_entity("ellie", "Agent", client=client).code)
            client.knot.assert_not_called()

    def test_invalid_input_never_contacts_store(self):
        for name, kind in (("bad name", "Person"), ("x\n", "Agent"), ("../x", "System"),
                           ("", "Person"), ("good", "Unknown")):
            client = self.client()
            self.assertEqual(2, memory.add_entity(name, kind, client=client).code)
            client.query.assert_not_called()

    def test_lookup_failure_bound_and_write_refusal(self):
        client = self.client()
        client.query.side_effect = [[], quipu.Unreachable("offline")]
        with self.assertRaises(quipu.Unreachable):
            memory.add_entity("new-name", "System", client=client)
        client.knot.assert_not_called()
        client = self.client([{}] * 10001)
        self.assertEqual(2, memory.add_entity("new-name", "System", client=client).code)
        client.knot.assert_not_called()
        client = self.client()
        client.knot.side_effect = quipu.Refused(["shape refused"])
        self.assertEqual(["shape refused"], memory.add_entity("new-name", "Agent", client=client).lines)

    def test_client_refuses_truncated_duplicate_scan(self):
        client = quipu.Client()
        with mock.patch.object(client, "_request", return_value={"rows": [], "truncated": True}):
            with self.assertRaises(quipu.QuipuError):
                client.query("SELECT", require_complete=True)

    def test_cli_dispatch(self):
        with mock.patch.object(memory, "add_entity", return_value=memory.Outcome(0, ["registered"])) as add:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, cli.main(["entity", "add", "new-name", "--type", "Person"]))
            add.assert_called_once_with("new-name", "Person")


if __name__ == "__main__":
    unittest.main()
