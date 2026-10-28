"""The four surfaces of one definition must agree: CLI, MCP, harness skills, man page.

docs/design/agent-memory-entities.md §3 "Drift gate". The generator check
(test_memory_tool_definitions) proves each file equals what the definitions
generate today. This file proves the surfaces agree with EACH OTHER as they
are served: the argparse CLI, the live `cy mcp` tool list, the Claude and
Codex skills, the slash commands and the man page all expose the same fields,
the same required set and the same recall filters.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from camayoc_cli import cli, definitions  # noqa: E402

DEFS = definitions.load()["entities"]
SKILLS = {"claude": ROOT / "skills/camayoc-memory/SKILL.md",
          "codex": ROOT / ".agents/skills/camayoc-memory/SKILL.md"}


def cli_choices():
    parser = cli.build_parser()
    sub = next(a for a in parser._actions if a.dest == "command").choices
    pick = lambda verb: next(a for a in sub[verb]._actions if a.dest == "entity").choices  # noqa: E731
    return pick("record"), pick("recall")


def mcp_session(*requests: dict) -> list[dict]:
    env = {**os.environ, "PYTHONPATH": str(ROOT), "QUIPU_SERVER": "http://127.0.0.1:9",
           "PYTHONDONTWRITEBYTECODE": "1"}
    payload = "".join(json.dumps(r) + "\n" for r in requests)
    proc = subprocess.run([sys.executable, "-m", "camayoc_cli", "mcp"], input=payload, env=env,
                          capture_output=True, text=True, timeout=20)
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


class SurfaceAgreementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        replies = mcp_session({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                               "params": {"protocolVersion": "2025-06-18"}},
                              {"jsonrpc": "2.0", "method": "notifications/initialized"},
                              {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        cls.init = replies[0]["result"]
        cls.tools = {t["name"]: t for t in replies[1]["result"]["tools"]}
        cls.record_cli, cls.recall_cli = cli_choices()

    def test_mcp_server_serves_the_generated_list(self):
        generated = json.loads((ROOT / "camayoc_cli/data/mcp-tools.json").read_text())["tools"]
        self.assertEqual([t["name"] for t in generated], list(self.tools))
        self.assertEqual("camayoc-memory", self.init["serverInfo"]["name"])
        self.assertIn("tools", self.init["capabilities"])

    def test_record_fields_agree_across_cli_mcp_skills_and_man(self):
        man = (ROOT / "docs/man/cy.1").read_text()
        skills = {k: p.read_text() for k, p in SKILLS.items()}
        record_cmd = (ROOT / "commands/record.md").read_text()
        for name, e in DEFS.items():
            with self.subTest(entity=name):
                fields = {f["name"]: f for f in e["fields"]}
                # CLI: every field is a flag spelled from sh:name.
                cli_flags = {s for a in self.record_cli[name]._actions for s in a.option_strings}
                self.assertTrue({f["flag"] for f in fields.values()} <= cli_flags)
                # MCP: same argument names and the same required set as the shape.
                schema = self.tools[e["verb"]]["inputSchema"]
                extras = set(e["sourceKind"].get("flags") or {}) | {"supersede", "dry_run"}
                self.assertEqual(set(fields), set(schema["properties"]) - extras)
                self.assertEqual({n for n, f in fields.items() if f["required"]}, set(schema["required"]))
                for n, f in fields.items():
                    self.assertEqual("array" if f["repeatable"] else "string", schema["properties"][n]["type"])
                    self.assertEqual(f["description"], schema["properties"][n]["description"])
                # Skills: one row per field with flag, MCP argument and the required column.
                for harness, text in skills.items():
                    text = text.split(f"\n### {name}\n", 1)[1].split("\n### ", 1)[0]
                    for n, f in fields.items():
                        row = re.search(rf"^\| {re.escape(n)} \| `{re.escape(f['flag'])}`[^|]*\| `{re.escape(n)}` \| "
                                        rf"(yes|no) \|", text, re.M)
                        self.assertIsNotNone(row, f"{harness} skill lacks {name}.{n}")
                        self.assertEqual("yes" if f["required"] else "no", row.group(1))
                # Slash command and man page name every required flag.
                required = [f["flag"] for f in fields.values() if f["required"]]
                self.assertIn(f"| {name} | " + " ".join(f"`{x}`" for x in required) + " |", record_cmd)
                for f in fields.values():
                    self.assertIn("\\fB" + f["flag"].replace("-", "\\-") + "\\fR", man)

    def test_recall_filters_agree(self):
        for name, e in DEFS.items():
            flags = {f["name"]: f["flag"] for f in e["fields"]}
            for r in e["recall"]:
                with self.subTest(query=r["name"]):
                    props = set(self.tools[r["verb"]]["inputSchema"]["properties"])
                    cli_flags = {s for a in self.recall_cli[name]._actions for s in a.option_strings}
                    if r.get("history"):
                        self.assertEqual({"key"}, props)
                        self.assertIn("--history", cli_flags)
                    else:
                        self.assertEqual(set(r["filters"]), props)
                        self.assertTrue({flags[x] for x in r["filters"]} <= cli_flags)

    def test_skills_carry_the_primer_verbatim(self):
        primer = (ROOT / "camayoc_cli/primer.md").read_text().rstrip()
        for harness, path in SKILLS.items():
            with self.subTest(harness=harness):
                text = path.read_text()
                body = text.split("<!-- BEGIN camayoc prime -->\n", 1)[1].split("\n<!-- END camayoc prime -->", 1)[0]
                self.assertEqual(primer, body, "regenerate with: python3 scripts/memory_tools.py")
        self.assertIn("[mcp_servers.camayoc-memory]", SKILLS["codex"].read_text())
        self.assertIn("/camayoc:record", SKILLS["claude"].read_text())

    def test_plugin_declares_the_memory_server(self):
        plugin = json.loads((ROOT / ".claude-plugin/plugin.json").read_text())
        server = plugin["mcpServers"]["camayoc-memory"]
        self.assertEqual(["${CLAUDE_PLUGIN_ROOT}/camayoc_cli/__main__.py", "mcp"], server["args"])

    def test_mcp_refusal_is_the_shape_message(self):
        replies = mcp_session({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                               "params": {"name": "record_directive", "arguments": {"rule": "Rebase first."}}},
                              {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                               "params": {"name": "record_reference", "arguments": {
                                   "subject": "s", "topic": "t",
                                   "locator": "password=" + "Zq8" * 6}}},
                              {"jsonrpc": "2.0", "id": 3, "method": "nope"})
        first = replies[0]["result"]
        self.assertTrue(first["isError"])
        issued_by = next(f for f in DEFS["directive"]["fields"] if f["name"] == "issuedBy")
        self.assertIn(issued_by["message"], first["content"][0]["text"])
        second = replies[1]["result"]
        self.assertTrue(second["isError"])
        self.assertIn("looks like a secret (labeled-assignment)", second["content"][0]["text"])
        self.assertNotIn("Zq8Zq8", second["content"][0]["text"])
        self.assertEqual(-32601, replies[2]["error"]["code"])


if __name__ == "__main__":
    unittest.main()
