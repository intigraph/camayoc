#!/usr/bin/env python3
"""jev-mcp — one way for any st agent to ask Jev for a typed decision.

Design: docs/design/jev-typed-decisions.md §8.1 (aegis-4hhqoe.1). A stdio MCP
server over `scripts/jev.py`, which stays the ONLY client — camayoc owns Jev
ingress and the discipline that every Jev answer is `inferred`. This module
adds the protocol surface and a bounded scheduled-work advisory adapter.
All inference uses the sole client and existing usage accountant; no call retries.

Six tools, using the sole JevClient and existing usage accountant:

    jev_noul(state, instructions, true?, false?)      -> yes/no probability
    jev_choice(state, instructions, criteria, ...)    -> winner + per-option probs
    jev_score(state, instructions, levels[2..10])     -> an ordered level
    jev_dry_run(kind, ...same args)                   -> the request, sent nowhere
    map_question(question)                            -> competency id + stored query,
                                                         or abstained / human_reads
    check_scheduled_work(title, body)                  -> advisory proposed seed label

The guardrails are BAKED IN rather than documented, because a guardrail an
agent has to remember is one the fleet will discover it forgot:

  * `jev_choice` defaults `none_text` ON. Jev cannot abstain — a choice always
    picks — so the none-of-these option is the only thing standing between a
    confident answer and a wrong one. Passing "" is how you opt out, and that
    is a deliberate act.
  * Every verdict carries `request`, `usage` and `model`. A verdict that cannot
    show its question is not a verdict.
  * A missing key is a TOOL ERROR the agent can read, never a lexical fallback
    under a Jev label.
  * `jev_dry_run` works with no key at all, so an agent can rehearse a request
    before spending anything.

USAGE ACCOUNTING. Every call appends one JSON line to the usage log naming the
agent (`SHANTY_AGENT`), the tool, the model, and the token counts Jev reported.
§8.1's cost line (~$0.0004 a decision, ~$4 at 10k/day) is only checkable if
somebody is counting, and the governor is asked to read exactly this.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import competency  # noqa: E402
import jev  # noqa: E402
import seed_schedule  # noqa: E402

PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "jev"
SERVER_VERSION = "0.1.0"

#: Where the per-agent call log lands. The deployment's own state dir when we
#: are inside one (that is where the governor already looks), else a plain
#: user state path so an ad-hoc run still accounts for itself.
USAGE_LOG_ENV = "JEV_USAGE_LOG"
DEFAULT_USAGE_LOG = "~/.local/state/camayoc-jev/usage.jsonl"

_STATE = {"type": "string",
          "description": "The text or JSON Jev judges. Keep it MINIMAL — long, "
                         "noisy state measurably degrades Jev."}
_INSTR = {"type": "string", "description": "The question, as instructions."}

#: The competency suite map_question maps onto: this checkout's own, so the
#: watermark in every verdict names exactly what was on disk.
SUITE_DIR = Path(__file__).resolve().parents[1] / "competency"


def usage_log_path(env: dict | None = None) -> Path:
    env = os.environ if env is None else env
    explicit = env.get(USAGE_LOG_ENV)
    if explicit:
        return Path(explicit).expanduser()
    # SHANTY_ROOT **IS** the store directory, not its parent — shantytown's own
    # help says `export SHANTY_ROOT=<path>/.shanty`. Appending ".shanty" again
    # produced `<...>/.shanty/.shanty/jev-usage.jsonl`, so accounting wrote to a
    # real file that nothing reads. Measured on the Mac (aegis-k6dcp9): the live
    # calls "did not account" until the doubled path was found; the governor is
    # asked to read this log, so a wrong path is a silent loss, not a cosmetic
    # one. The failure needed a host with SHANTY_ROOT set to surface at all.
    root = env.get("SHANTY_ROOT")
    if root:
        return Path(root).expanduser() / "jev-usage.jsonl"
    return Path(DEFAULT_USAGE_LOG).expanduser()


def record_usage(tool: str, out: dict, env: dict | None = None) -> None:
    """Append one accounting line. NEVER fails the call it is accounting for:
    a full disk or an unwritable state dir must not turn a good decision into
    an error the agent then retries."""
    env = os.environ if env is None else env
    usage = out.get("usage") or {}
    line = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "agent": env.get("SHANTY_AGENT") or env.get("BEADS_ACTOR") or "unknown",
        "tool": tool,
        "model": out.get("model"),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
    }
    try:
        path = usage_log_path(env)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as fh:
            fh.write(json.dumps(line) + "\n")
    except OSError:
        pass


# -- the request builders, shared by the live tools and by jev_dry_run --------

def build_question(kind: str, args: dict) -> dict:
    """One place that turns tool arguments into a Jev question, so a dry run
    rehearses the SAME request the live tool would send. Two builders would
    drift, and the drift would be invisible precisely because dry-run is what
    you would use to check."""
    instructions = args.get("instructions")
    if not isinstance(instructions, str) or not instructions.strip():
        raise ValueError("instructions is required and must be a non-empty string")
    if kind == "noul":
        return jev.JevClient.noul_q(instructions, args.get("true"), args.get("false"))
    if kind == "choice":
        criteria = args.get("criteria")
        if not isinstance(criteria, dict) or not criteria:
            raise ValueError("choice needs a criteria object mapping option id -> text")
        if not all(isinstance(v, str) for v in criteria.values()):
            raise ValueError("every criteria value must be a string")
        # DEFAULT ON. Absent key -> the default text; explicit "" -> omitted.
        none_text = args.get("none_text", "None of these fit")
        return jev.JevClient.choice_q(instructions, criteria, none_text or None)
    if kind == "score":
        levels = args.get("levels")
        if not isinstance(levels, list) or not all(isinstance(v, str) for v in levels):
            raise ValueError("score needs `levels`: a list of 2..10 ordered strings")
        return jev.JevClient.score_q(instructions, levels)
    raise ValueError(f"unknown kind {kind!r}; expected noul, choice or score")


def _state_of(args: dict):
    if "state" not in args:
        raise ValueError("state is required")
    return args["state"]


def call_tool(name: str, args: dict, client_factory=jev.JevClient, env: dict | None = None) -> dict:
    """Run one tool. Returns the verdict dict; raises ValueError for a bad
    request and jev.JevError for anything the service did."""
    if name == "check_scheduled_work":
        verdict = seed_schedule.classify(args.get("title"), args.get("body", ""), client_factory())
        record_usage(name, verdict, env)
        return verdict
    if name == "jev_dry_run":
        kind = args.get("kind")
        if kind not in ("noul", "choice", "score"):
            raise ValueError("kind must be one of noul, choice, score")
        question = build_question(kind, args)
        return {"request": {"state": _state_of(args), "model": jev.MODEL,
                            "questions": {"q": question}},
                "sent": False,
                "note": "dry run — nothing was sent and nothing was spent."}
    if name == "map_question":
        asked = args.get("question")
        if not isinstance(asked, str) or not asked.strip():
            raise ValueError("question is required and must be a non-empty string")
        suite = competency.parse_suite(SUITE_DIR)
        if not suite:
            raise ValueError(f"no competency questions parsed from {SUITE_DIR}")
        verdict = competency.map_question(asked.strip(), suite,
                                          competency.JevScorer(client_factory()))
        record_usage(name, verdict, env)
        return verdict
    kind = {"jev_noul": "noul", "jev_choice": "choice", "jev_score": "score"}.get(name)
    if kind is None:
        raise ValueError(f"unknown tool {name!r}")
    question = build_question(kind, args)
    state = _state_of(args)
    out = client_factory().ask(state, {"q": question})
    answer = out["answers"].get("q", {})
    verdict = {"answer": answer, "request": out["request"],
               "usage": out.get("usage", {}), "model": out.get("model")}
    if kind == "choice":
        # Say OUT LOUD when the winner is the escape hatch. The agent would
        # otherwise have to know that `none-of-these` is special, and an agent
        # that does not know reads "nothing fits, 0.91" as a confident pick.
        verdict["chose_none"] = answer.get("choice") == jev.NONE_OPTION
    if kind == "score":
        verdict["levels"] = list(args.get("levels", []))
    record_usage(name, verdict, env)
    return verdict


TOOLS = [
    {
        "name": "check_scheduled_work",
        "description": (
            "Advisory seed create/update preflight. Ask two atomic Jev questions: "
            "scheduled operational work and explicit Chaski/Quipu trigger. "
            "Returns a proposed needs-chaski label, probabilities and request; "
            "never writes a tracker, refuses a write or promotes model facts. "
            "Paid call; invoke only within the caller's admitted budget."),
        "inputSchema": {"type": "object", "properties": {
            "title": {"type": "string"}, "body": {"type": "string"}},
            "required": ["title", "body"]},
    },
    {
        "name": "jev_noul",
        "description": (
            "Ask Jev one yes/no question about a state and get a calibrated "
            "probability. Use for a bounded, repeated judgment where a number "
            "beats a rationale. Jev gives NO rationale — record the state. "
            "State the ABSTAIN DIRECTION inside the question ('if no level is "
            "stated, answer no'), or an unstated fact reads as a false yes."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "state": _STATE, "instructions": _INSTR,
                "true": {"type": "string", "description": "What a YES means (optional criteria)."},
                "false": {"type": "string", "description": "What a NO means (optional criteria)."},
            },
            "required": ["state", "instructions"],
        },
    },
    {
        "name": "jev_choice",
        "description": (
            "Ask Jev to pick one of up to 255 named options for a state. "
            "Jev CANNOT ABSTAIN — a none-of-these option is added by default "
            "and `chose_none` is returned; read `confidence` before acting. "
            "Never let a choice answer override a fact a noul established."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "state": _STATE, "instructions": _INSTR,
                "criteria": {"type": "object", "additionalProperties": {"type": "string"},
                             "description": "option id -> what that option means."},
                "none_text": {"type": "string",
                              "description": "Text for the none-of-these option. Defaults ON; "
                                             "pass \"\" to omit it deliberately."},
            },
            "required": ["state", "instructions", "criteria"],
        },
    },
    {
        "name": "jev_score",
        "description": (
            "Ask Jev to place a state on an ordered scale of 2..10 named "
            "levels, lowest first. The level names are returned with the "
            "answer — a score means nothing without its ladder."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "state": _STATE, "instructions": _INSTR,
                "levels": {"type": "array", "items": {"type": "string"}, "minItems": 2,
                           "maxItems": 10, "description": "Ordered levels, lowest first."},
            },
            "required": ["state", "instructions", "levels"],
        },
    },
    {
        "name": "map_question",
        "description": (
            "Map a freeform question onto camayoc's competency suite BEFORE "
            "writing SPARQL by hand. Returns `outcome`: `mapped` (competency_id "
            "at confidence >= 0.75, plus `stored_query` — if its state is STORED, "
            "run that named query), `abstained` (no competency question fits: "
            "file the asked question as a CANDIDATE competency question), or "
            "`human_reads` (a low-confidence `candidate` — not an answer, never "
            "act on it). Every verdict is inferred; never promote it."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string",
                             "description": "The question you are about to ask the graph, as you would phrase it."},
            },
            "required": ["question"],
        },
    },
    {
        "name": "jev_dry_run",
        "description": (
            "Build the exact request one of the other tools would send and "
            "return it WITHOUT sending it. Works with no API key. Use to "
            "rehearse a question and check the state you assembled."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["noul", "choice", "score"]},
                "state": _STATE, "instructions": _INSTR,
                "true": {"type": "string"}, "false": {"type": "string"},
                "criteria": {"type": "object", "additionalProperties": {"type": "string"}},
                "none_text": {"type": "string"},
                "levels": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["kind", "state", "instructions"],
        },
    },
]


# -- JSON-RPC / MCP ----------------------------------------------------------

def handle(message: dict, client_factory=jev.JevClient, env: dict | None = None) -> dict | None:
    """One request in, one response out. Returns None for a NOTIFICATION —
    a notification that gets a reply is a protocol error, and the client is
    entitled to drop the connection over it."""
    method = message.get("method")
    mid = message.get("id")
    is_notification = "id" not in message

    if method == "initialize":
        result = {"protocolVersion": PROTOCOL_VERSION,
                  "capabilities": {"tools": {}},
                  "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION}}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "ping":
        result = {}
    elif method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        try:
            verdict = call_tool(name, args, client_factory, env)
        except (ValueError, jev.JevError) as exc:
            # isError, NOT a JSON-RPC error: the agent is meant to READ this and
            # decide, and a transport-level error is not addressed to the model.
            # A missing key lands here, which is the point — it must never look
            # like an answer.
            return {"jsonrpc": "2.0", "id": mid,
                    "result": {"content": [{"type": "text", "text": f"jev: {exc}"}],
                               "isError": True}}
        return {"jsonrpc": "2.0", "id": mid,
                "result": {"content": [{"type": "text",
                                        "text": json.dumps(verdict, indent=2)}],
                           "structuredContent": verdict, "isError": False}}
    elif is_notification:
        return None
    else:
        return {"jsonrpc": "2.0", "id": mid,
                "error": {"code": -32601, "message": f"method not found: {method}"}}

    if is_notification:
        return None
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def serve(stdin=None, stdout=None) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            stdout.write(json.dumps({"jsonrpc": "2.0", "id": None,
                                     "error": {"code": -32700, "message": "parse error"}}) + "\n")
            stdout.flush()
            continue
        response = handle(message)
        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(serve())
