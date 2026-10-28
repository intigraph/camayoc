"""`cy mcp`: the generated record_*/recall_* tools over MCP stdio (JSON-RPC 2.0).

The tool list is data/mcp-tools.json exactly as scripts/memory_tools.py
generated it, so the MCP surface cannot drift from the CLI: both read the
same definitions, and a call runs the same memory.record / memory.recall the
CLI runs. A refusal comes back as an `isError` result carrying the shape's
plain-word message.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from . import __version__, definitions, memory, quipu

TOOLS = Path(__file__).resolve().parent / "data" / "mcp-tools.json"
PROTOCOL = "2025-06-18"


def tools() -> list[dict]:
    return json.loads(TOOLS.read_text(encoding="utf-8"))["tools"]


def _routes() -> dict[str, tuple[str, str, dict | None]]:
    out = {}
    for name, e in definitions.load()["entities"].items():
        out[e["verb"]] = ("record", name, None)
        for r in e["recall"]:
            out[r["verb"]] = ("recall", name, r)
    return out


def call(name: str, arguments: dict) -> memory.Outcome:
    route = _routes().get(name)
    if route is None:
        return memory.Outcome(2, [f"unknown tool {name}"])
    kind, entity_name, recall_def = route
    entity = definitions.load()["entities"][entity_name]
    try:
        if kind == "record":
            values = {f["name"]: arguments.get(f["name"]) for f in entity["fields"]}
            flags = {k: bool(arguments.get(k)) for k in (entity["sourceKind"].get("flags") or {})}
            if arguments.get("dry_run"):
                return memory.record(entity_name, values, supersede=arguments.get("supersede"),
                                     flags=flags, dry_run=True)
            return memory.record_or_queue(entity_name, values, supersede=arguments.get("supersede"), flags=flags)
        filters = {f: arguments.get(f) for f in recall_def.get("filters", [])}
        return memory.recall(entity_name, filters, history=arguments.get("key") if recall_def.get("history") else None)
    except quipu.QuipuError as exc:
        return memory.Outcome(3, [f"camayoc: {exc}"])
    except Exception as exc:  # noqa: BLE001 - one bad call must never take the server down
        return memory.Outcome(3, [f"camayoc: could not run {name} ({type(exc).__name__}); "
                                  "the server is still running"])


def handle(msg) -> dict | None:
    if not isinstance(msg, dict):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}}
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:
        return None  # notification
    if method == "initialize":
        version = (msg.get("params") or {}).get("protocolVersion") or PROTOCOL
        result = {"protocolVersion": version, "capabilities": {"tools": {}},
                  "serverInfo": {"name": "camayoc-memory", "version": __version__},
                  "instructions": "Agent memory: record_<entity> writes one typed record through quipu's shapes; "
                                  "recall_<entity> reads current records. Refusals name the missing field."}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": tools()}
    elif method == "tools/call":
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        out = call(str(params.get("name", "")), arguments)
        text = "\n".join(out.lines)
        result = {"content": [{"type": "text", "text": text}], "isError": out.code != 0}
        if out.data:
            result["structuredContent"] = out.data
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def serve(stdin=None, stdout=None) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            reply = handle(msg)
        if reply is not None:
            stdout.write(json.dumps(reply) + "\n")
            stdout.flush()
    return 0
