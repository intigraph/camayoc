"""jev-mcp — the promises an st agent relies on without being able to check them.

No network anywhere. What is pinned:
  * the none-of-these option is ON by default, and `chose_none` is reported —
    Jev cannot abstain, so a caller that forgets is still protected;
  * a missing key is a READABLE TOOL ERROR, never an answer and never a
    lexical fallback under a Jev label;
  * jev_dry_run works with NO key and rehearses the SAME request the live tool
    would send — two builders would drift invisibly;
  * every verdict carries request + usage + model;
  * usage is accounted per SHANTY_AGENT, and an unwritable log does not fail
    the decision it is accounting for;
  * a notification gets NO response.
"""
from __future__ import annotations

import io
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import jev  # noqa: E402
import jev_mcp  # noqa: E402


class FakeTransport:
    """Plays the API. Records every body it was handed."""

    def __init__(self, answer: dict | None = None):
        self.bodies: list[dict] = []
        self.answer = answer if answer is not None else {"noul": 0.75}

    def __call__(self, body, key):
        self.bodies.append(body)
        return {"answers": {"q": self.answer}, "model": "jev-1.13.0",
                "usage": {"input_tokens": 11, "output_tokens": 3}}


def client_factory(transport):
    return lambda: jev.JevClient(api_key="k", transport=transport)


def call(name, args, transport=None, env=None):
    transport = transport or FakeTransport()
    return jev_mcp.call_tool(name, args, client_factory(transport), env or {})


class ChoiceDefaultsNoneOn(unittest.TestCase):
    def test_none_option_is_added_without_being_asked_for(self):
        t = FakeTransport({"choice": "billing", "probabilities": {"billing": 0.9}})
        call("jev_choice", {"state": "s", "instructions": "who?",
                            "criteria": {"billing": "money"}}, t)
        options = t.bodies[0]["questions"]["q"]["criteria"]
        self.assertIn(jev.NONE_OPTION, options)

    def test_empty_none_text_omits_it_deliberately(self):
        t = FakeTransport({"choice": "billing", "probabilities": {}})
        call("jev_choice", {"state": "s", "instructions": "who?", "none_text": "",
                            "criteria": {"billing": "money"}}, t)
        self.assertNotIn(jev.NONE_OPTION, t.bodies[0]["questions"]["q"]["criteria"])

    def test_choosing_none_is_reported_out_loud(self):
        t = FakeTransport({"choice": jev.NONE_OPTION, "probabilities": {}, "confidence": 0.91})
        out = call("jev_choice", {"state": "s", "instructions": "who?",
                                  "criteria": {"billing": "money"}}, t)
        self.assertTrue(out["chose_none"])

    def test_a_real_pick_is_not_flagged_as_none(self):
        t = FakeTransport({"choice": "billing", "probabilities": {}})
        out = call("jev_choice", {"state": "s", "instructions": "who?",
                                  "criteria": {"billing": "money"}}, t)
        self.assertFalse(out["chose_none"])


class MissingKeyIsAReadableError(unittest.TestCase):
    def test_no_key_is_an_error_the_agent_can_read_not_an_answer(self):
        clean = {k: v for k, v in os.environ.items() if k not in (jev.KEY_ENV,)}
        clean[jev.KEY_FILE_ENV] = "/nonexistent/typesafe-key"
        real_home = os.environ.get("HOME")
        try:
            os.environ[jev.KEY_FILE_ENV] = "/nonexistent/typesafe-key"
            os.environ.pop(jev.KEY_ENV, None)
            os.environ["HOME"] = "/nonexistent-home"
            reply = jev_mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                    "params": {"name": "jev_noul",
                                               "arguments": {"state": "s",
                                                             "instructions": "q?"}}})
        finally:
            os.environ.pop(jev.KEY_FILE_ENV, None)
            if real_home is not None:
                os.environ["HOME"] = real_home
        self.assertTrue(reply["result"]["isError"])
        text = reply["result"]["content"][0]["text"]
        self.assertIn("typesafe_api_key", text)
        # It must NOT smuggle an answer alongside the error.
        self.assertNotIn("structuredContent", reply["result"])


class DryRunNeedsNoKeyAndMatchesTheLiveRequest(unittest.TestCase):
    def test_dry_run_sends_nothing(self):
        out = call("jev_dry_run", {"kind": "noul", "state": "s", "instructions": "q?"})
        self.assertFalse(out["sent"])
        self.assertEqual(out["request"]["questions"]["q"]["type"], "noul")

    def test_dry_run_request_is_byte_identical_to_what_the_live_tool_sends(self):
        args = {"state": "s", "instructions": "who?", "criteria": {"billing": "money"}}
        t = FakeTransport({"choice": "billing", "probabilities": {}})
        live = call("jev_choice", dict(args), t)
        dry = call("jev_dry_run", {"kind": "choice", **args})
        self.assertEqual(dry["request"]["questions"], live["request"]["questions"])
        self.assertEqual(dry["request"]["state"], live["request"]["state"])


class EveryVerdictShowsItsQuestion(unittest.TestCase):
    def test_request_usage_and_model_ride_along(self):
        out = call("jev_noul", {"state": "s", "instructions": "q?"})
        self.assertEqual(out["request"]["state"], "s")
        self.assertEqual(out["usage"], {"input_tokens": 11, "output_tokens": 3})
        self.assertEqual(out["model"], "jev-1.13.0")

    def test_score_returns_the_ladder_its_number_indexes(self):
        t = FakeTransport({"score": "high"})
        out = call("jev_score", {"state": "s", "instructions": "how bad?",
                                 "levels": ["low", "high"]}, t)
        self.assertEqual(out["levels"], ["low", "high"])


class BadRequestsAreRefusedBeforeSpending(unittest.TestCase):
    def test_score_rejects_one_level(self):
        t = FakeTransport()
        with self.assertRaises(ValueError):
            call("jev_score", {"state": "s", "instructions": "q", "levels": ["only"]}, t)
        self.assertEqual(t.bodies, [])

    def test_choice_without_criteria_is_refused(self):
        with self.assertRaises(ValueError):
            call("jev_choice", {"state": "s", "instructions": "q"})

    def test_missing_state_is_refused(self):
        with self.assertRaises(ValueError):
            call("jev_noul", {"instructions": "q"})

    def test_blank_instructions_are_refused(self):
        with self.assertRaises(ValueError):
            call("jev_noul", {"state": "s", "instructions": "   "})


class UsageIsCountedPerAgent(unittest.TestCase):
    def test_one_line_per_call_naming_the_agent(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "usage.jsonl"
            env = {"SHANTY_AGENT": "wu", jev_mcp.USAGE_LOG_ENV: str(log)}
            call("jev_noul", {"state": "s", "instructions": "q?"}, env=env)
            call("jev_noul", {"state": "s", "instructions": "q?"}, env=env)
            rows = [json.loads(x) for x in log.read_text().splitlines()]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["agent"], "wu")
        self.assertEqual(rows[0]["tool"], "jev_noul")
        self.assertEqual(rows[0]["input_tokens"], 11)

    def test_an_unwritable_log_does_not_fail_the_decision(self):
        env = {"SHANTY_AGENT": "wu", jev_mcp.USAGE_LOG_ENV: "/proc/nope/usage.jsonl"}
        out = call("jev_noul", {"state": "s", "instructions": "q?"}, env=env)
        self.assertEqual(out["model"], "jev-1.13.0")

    def test_SHANTY_ROOT_is_the_store_dir_and_is_not_doubled(self):
        """Regression (aegis-k6dcp9): this appended a second ".shanty", so the
        log landed at <root>/.shanty/.shanty/... — a real file nothing reads.
        Only a host with SHANTY_ROOT set could surface it, which is why it
        survived the vati acceptance run."""
        self.assertEqual(jev_mcp.usage_log_path({"SHANTY_ROOT": "/srv/dep/.shanty"}),
                         Path("/srv/dep/.shanty/jev-usage.jsonl"))

    def test_an_explicit_log_path_still_wins_over_SHANTY_ROOT(self):
        self.assertEqual(
            jev_mcp.usage_log_path({"SHANTY_ROOT": "/srv/dep/.shanty",
                                    jev_mcp.USAGE_LOG_ENV: "/tmp/explicit.jsonl"}),
            Path("/tmp/explicit.jsonl"))

    def test_a_dry_run_costs_nothing_so_it_is_not_counted(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "usage.jsonl"
            env = {"SHANTY_AGENT": "wu", jev_mcp.USAGE_LOG_ENV: str(log)}
            call("jev_dry_run", {"kind": "noul", "state": "s", "instructions": "q?"}, env=env)
            self.assertFalse(log.exists())


class ProtocolBasics(unittest.TestCase):
    def test_initialize_declares_tools(self):
        r = jev_mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        self.assertIn("tools", r["result"]["capabilities"])
        self.assertEqual(r["result"]["serverInfo"]["name"], "jev")

    def test_tools_list_includes_the_advisory_seed_preflight(self):
        r = jev_mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = sorted(t["name"] for t in r["result"]["tools"])
        self.assertEqual(names, ["check_scheduled_work", "jev_choice", "jev_dry_run", "jev_noul", "jev_score", "map_question"])

    def test_every_tool_declares_a_usable_schema(self):
        r = jev_mcp.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
        for tool in r["result"]["tools"]:
            self.assertEqual(tool["inputSchema"]["type"], "object")
            self.assertTrue(tool["description"].strip())
            for req in tool["inputSchema"]["required"]:
                self.assertIn(req, tool["inputSchema"]["properties"])

    def test_a_notification_gets_no_response(self):
        self.assertIsNone(jev_mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_unknown_method_is_a_protocol_error(self):
        r = jev_mcp.handle({"jsonrpc": "2.0", "id": 4, "method": "nope"})
        self.assertEqual(r["error"]["code"], -32601)

    def test_serve_speaks_line_delimited_json_and_skips_notifications(self):
        lines = "\n".join([
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        ]) + "\n"
        out = io.StringIO()
        jev_mcp.serve(io.StringIO(lines), out)
        replies = [json.loads(x) for x in out.getvalue().splitlines()]
        self.assertEqual([r["id"] for r in replies], [1, 2])

    def test_malformed_json_gets_a_parse_error_not_a_crash(self):
        out = io.StringIO()
        jev_mcp.serve(io.StringIO("{not json\n"), out)
        self.assertEqual(json.loads(out.getvalue())["error"]["code"], -32700)


class MappingTransport:
    """Plays Jev for the one `mapping` choice map_question poses."""

    def __init__(self, choice, confidence, prob=0.9):
        self.bodies = []
        self.choice, self.confidence, self.prob = choice, confidence, prob

    def __call__(self, body, key):
        self.bodies.append(body)
        return {"answers": {"mapping": {"type": "choice", "choice": self.choice,
                                        "probabilities": {self.choice: self.prob},
                                        "confidence": self.confidence}},
                "model": "jev-1.13.0", "usage": {"input_tokens": 4700, "output_tokens": 0}}


class MapQuestion(unittest.TestCase):
    """aegis-4hhqoe.10: three outcomes, the 0.75 floor, coverage kept separate."""

    def run_map(self, transport, question="what did it cost to reach decision D?"):
        return jev_mcp.call_tool("map_question", {"question": question},
                                 client_factory(transport), {"JEV_USAGE_LOG": os.devnull})

    def test_mapped_above_floor_carries_the_stored_query(self):
        t = MappingTransport("verification-and-liveness#21", 0.96)
        v = self.run_map(t)
        self.assertEqual(v["outcome"], "mapped")
        self.assertEqual(v["competency_id"], "verification-and-liveness#21")
        self.assertEqual(v["stored_query"], {"state": "STORED", "query": "camayoc_decision_cost"})
        self.assertEqual(v["source_kind"], "inferred")
        self.assertTrue(v["suite_watermark"].startswith("sha256:"))
        self.assertEqual(len(t.bodies), 1, "one Jev call per asked question")
        # the whole suite was offered, plus the none option
        crit = t.bodies[0]["questions"]["mapping"]["criteria"]
        self.assertIn(jev.NONE_OPTION, crit)
        self.assertEqual(len(crit) - 1, v["suite_size"])

    def test_mapped_to_an_unwritten_question_says_so_rather_than_naming_a_query(self):
        v = self.run_map(MappingTransport("crew-task-lifecycle#1", 0.96))
        self.assertEqual(v["outcome"], "mapped")
        self.assertEqual(v["stored_query"]["state"], "UNWRITTEN")
        self.assertIsNone(v["stored_query"]["query"])

    def test_below_floor_is_human_reads_never_an_answer(self):
        v = self.run_map(MappingTransport("verification-and-liveness#21", 0.64))
        self.assertEqual(v["outcome"], "human_reads")
        self.assertIsNone(v["competency_id"])
        self.assertIsNone(v["stored_query"])
        self.assertEqual(v["candidate"], "verification-and-liveness#21")

    def test_floor_is_inclusive_at_exactly_075(self):
        self.assertEqual(self.run_map(MappingTransport("verification-and-liveness#21", 0.75))["outcome"],
                         "mapped")

    def test_none_of_these_is_abstained(self):
        v = self.run_map(MappingTransport(jev.NONE_OPTION, 0.99))
        self.assertEqual(v["outcome"], "abstained")
        self.assertTrue(v["abstained"])
        self.assertIsNone(v["competency_id"])

    def test_an_id_outside_the_suite_is_not_mapped(self):
        self.assertEqual(self.run_map(MappingTransport("invented#1", 0.99))["outcome"], "human_reads")

    def test_empty_question_is_a_tool_error(self):
        with self.assertRaises(ValueError):
            jev_mcp.call_tool("map_question", {"question": "  "}, client_factory(MappingTransport("x", 1)), {})

    def test_no_key_is_an_error_the_agent_reads(self):
        def no_key():
            raise jev.JevUnavailable("no Jev key")
        r = jev_mcp.handle({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                            "params": {"name": "map_question", "arguments": {"question": "q?"}}},
                           no_key, {})
        self.assertTrue(r["result"]["isError"])


class KeyResolution(unittest.TestCase):
    def test_env_wins_over_file(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".key", delete=False) as fh:
            fh.write("from-file-0123456789abcdef")
            name = fh.name
        self.assertEqual(jev.resolve_key({jev.KEY_ENV: "from-env-0123456789abcdef",
                                          jev.KEY_FILE_ENV: name}), "from-env-0123456789abcdef")
        self.assertEqual(jev.resolve_key({jev.KEY_FILE_ENV: name}), "from-file-0123456789abcdef")
        os.unlink(name)

    def test_no_key_anywhere_is_empty_not_an_exception(self):
        self.assertEqual(jev.resolve_key({jev.KEY_FILE_ENV: "/nonexistent",
                                          "HOME": "/nonexistent-home"}), "")

    def test_the_default_path_is_expanded_against_the_PASSED_env(self):
        """Path.expanduser() reads the process HOME and ignores a passed env, so
        this pinned the one thing that made the check above meaningful. Without
        it, resolve_key({...}) answered from the REAL home and the test passed
        or failed on whether this machine has a key (aegis-g69atf shape)."""
        import tempfile
        with tempfile.TemporaryDirectory() as home:
            cfg = Path(home) / ".config" / "aegis"
            cfg.mkdir(parents=True)
            (cfg / "typesafe_api_key").write_text("sandbox-key-0123456789abcdef\n")
            self.assertEqual(jev.resolve_key({"HOME": home}), "sandbox-key-0123456789abcdef")
            # and the real home is NOT consulted when an env is passed
            self.assertEqual(jev.resolve_key({"HOME": "/nonexistent-home"}), "")


if __name__ == "__main__":
    unittest.main()
