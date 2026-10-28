"""`camayoc` / `cy`: the command line, built from the generated definitions.

Every `record` flag, its help and its refusal come from
shapes/agent-memory.shapes.ttl through data/definitions.json; nothing about a
field is spelled here. Required fields are NOT argparse-required on purpose:
a missing field must refuse with the shape's plain-word sh:message, not with
argparse's usage error.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

from . import __version__, definitions, memory, prime, quipu


def _add_field(p: argparse.ArgumentParser, f: dict) -> None:
    help_text = f["description"] + (" Required." if f["required"] else "")
    if f["enum"]:
        help_text += f" One of: {', '.join(f['enum'])}."
    kwargs = {"dest": f["name"], "help": help_text, "metavar": f["name"].upper()}
    if f["repeatable"]:
        kwargs["action"] = "append"
    p.add_argument(f["flag"], **kwargs)


def build_parser() -> argparse.ArgumentParser:
    defs = definitions.load()
    ap = argparse.ArgumentParser(
        prog="cy", description="camayoc agent memory: record and recall typed entities in Quipu. "
                               "Run `cy prime` for the agent primer.")
    ap.add_argument("--version", action="version", version=f"camayoc {__version__}")
    sub = ap.add_subparsers(dest="command", metavar="<command>")

    rec = sub.add_parser("record", help="record one entity (writes through quipu's shapes)")
    rec_sub = rec.add_subparsers(dest="entity", metavar="<entity>")
    rcl = sub.add_parser("recall", help="recall current entities (stored queries)")
    rcl_sub = rcl.add_subparsers(dest="entity", metavar="<entity>")
    for name, entity in sorted(defs["entities"].items()):
        p = rec_sub.add_parser(name, help=entity["summary"], description=entity["summary"],
                               epilog=memory.example_line(entity),
                               formatter_class=argparse.RawDescriptionHelpFormatter)
        for f in entity["fields"]:
            _add_field(p, f)
        for flag, spec in (entity["sourceKind"].get("flags") or {}).items():
            p.add_argument(f"--{flag}", action="store_true", help=spec["help"])
        p.add_argument("--supersede", metavar="IRI", help="confirm replacing a ruling by someone else (that record's IRI)")
        p.add_argument("--dry-run", action="store_true", help="print the record; write nothing")
        p.add_argument("--json", action="store_true", help="print the outcome as JSON")

        q = rcl_sub.add_parser(name, help=entity["recall"][0]["summary"],
                               description=" ".join(r["summary"] for r in entity["recall"]))
        seen = set()
        by_name = {f["name"]: f for f in entity["fields"]}
        for r in entity["recall"]:
            for flt in r.get("filters", []):
                if flt not in seen:
                    seen.add(flt)
                    q.add_argument(by_name[flt]["flag"], dest=flt, metavar=flt.upper(),
                                   help=by_name[flt]["description"] + " Optional filter.")
            if r.get("history"):
                q.add_argument("--history", metavar="KEY", help="every version of one natural key (IRI or key path)")
        q.add_argument("--json", action="store_true", help="print rows as JSON")

    topic = sub.add_parser("topic", help="manage registered memory topics")
    topic_sub = topic.add_subparsers(dest="topic_command", metavar="<action>")
    add = topic_sub.add_parser("add", help="register a memory topic (natural-key part)")
    add.add_argument("name")
    add.add_argument("--alias", action="append", default=[], help="another spelling; repeat")

    flush = sub.add_parser("flush", help="replay records queued while quipu was unreachable")
    flush.add_argument("--hook", action="store_true", help="start bounded detached replay; always exits 0")
    flush.add_argument("--hook-worker", action="store_true", help=argparse.SUPPRESS)
    sub.add_parser("backlog", help="read the local queue count as JSON (unknown stays null)")

    note = sub.add_parser("note", help="capture a note now (untyped; promotion types it later)")
    note.add_argument("text")
    note.add_argument("--kind", choices=["directive", "decision"], help="mark it a promotion candidate")
    note.add_argument("--about", help="the entity it is about (IRI or local name)")
    note.add_argument("--source", default="explicit", choices=["explicit", "stop-check", "file-redirect", "import"],
                      help="which producer captured it (default explicit)")
    note.add_argument("--json", action="store_true")

    cap = sub.add_parser("capture", help="capture spool (written by the harness hooks)")
    cap_sub = cap.add_subparsers(dest="capture_command", metavar="<action>")
    cap_sub.add_parser("flush", help="write spooled captures to quipu")
    quarantine = cap_sub.add_parser("quarantine", help="inspect local rejected/expired capture counts (no network)")
    action = quarantine.add_mutually_exclusive_group()
    action.add_argument("--clear", action="store_true", help="discard all quarantined captures locally")
    action.add_argument("--requeue", action="store_true", help="return eligible captures to the pending tail without network writes")
    quarantine.add_argument("--json", action="store_true")


    pro = sub.add_parser("promote", help="promote captured rulings and cy calls to typed entities")
    pro.add_argument("mode", nargs="?", choices=["adapter"], help="adapter: chaski incremental protocol")
    pro.add_argument("--describe", action="store_true", help="adapter: subscription metadata")
    pro.add_argument("--changes", action="store_true", help="adapter: one request as JSON on stdin")
    pro.add_argument("--session", help="only this session's captures")
    pro.add_argument("--retry-blocked", action="store_true", help="try blocked captures again")
    pro.add_argument("--pending", action="store_true", help="list blocked captures with the cy call that finishes each")
    pro.add_argument("--event", action="store_true", help="chaski command sink: one event JSON on stdin")
    pro.add_argument("--json", action="store_true")
    sub.add_parser("mcp", help="serve the generated record_*/recall_* MCP tools on stdio")

    hook = sub.add_parser("hook", help="harness hooks (installed by tambo; off unless wired)")
    hook_sub = hook.add_subparsers(dest="hook_name", metavar="<hook>")
    mr = hook_sub.add_parser("memory-redirect",
                             help="PreToolUse on Write|Edit|MultiEdit (Codex: apply_patch): move writes under "
                                  "*/.claude/projects/*/memory/* into camayoc entities")
    mr.add_argument("--mode", choices=("off", "copy", "block"),
                    help="copy (default): allow the write, record it in the background; block: refuse it and "
                         "print the cy record call; off: do nothing. CAMAYOC_MEMORY_REDIRECT overrides this.")
    mr.add_argument("--person", metavar="IRI",
                    help="who issues rulings/preferences in these files (default $CAMAYOC_PERSON)")
    mr.add_argument("--worker", metavar="JOB", help=argparse.SUPPRESS)
    for event, what in (("user-prompt-submit", "capture the prompt"), ("stop", "capture the final answer"),
                        ("session-end", "flush captures and promote this session's candidates")):
        hook_sub.add_parser(event, help=f"memory capture: {what} (hook JSON on stdin; always exits 0, prints nothing)")

    pr = sub.add_parser("prime", help="agent primer and live state (stack-prime/1)")
    pr.add_argument("--brief", action="store_true")
    pr.add_argument("--json", action="store_true")
    pr.add_argument("--static", action="store_true")
    pr.add_argument("--section")
    return ap


def _emit(out: memory.Outcome, as_json: bool) -> int:
    if as_json:
        print(json.dumps({"code": out.code, "lines": out.lines, **out.data}, indent=2, sort_keys=True))
    else:
        stream = sys.stdout if out.code == 0 else sys.stderr
        for line in out.lines:
            print(line, file=stream)
    return out.code


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    if args.command is None:
        ap.print_help()
        return 2
    if args.command == "backlog":
        from . import offline
        print(json.dumps(offline.backlog()))
        return 0
    if args.command == "flush" and (args.hook or args.hook_worker):
        try:
            if args.hook_worker:
                memory.flush_all(limit=20, timeout=0.75)
            else:
                # No count/scan or mutation lock on the harness thread.
                subprocess.Popen([sys.executable, str(Path(__file__).with_name('__main__.py')),
                                  'flush', '--hook-worker'], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
        except Exception:  # hook failures must never block the harness
            print("camayoc: offline replay unavailable; backlog unconfirmed", file=sys.stderr)
        return 0
    if args.command == "mcp":
        from . import mcp
        return mcp.serve()
    if args.command == "hook":
        if args.hook_name == "memory-redirect":
            from . import memory_redirect
            return memory_redirect.main(args.mode, args.person, args.worker)
        if args.hook_name not in ("user-prompt-submit", "stop", "session-end"):
            ap.parse_args(["hook", "--help"])
        from . import capture
        try:
            payload = json.load(sys.stdin)
        except (ValueError, OSError):
            payload = {}
        capture.hook(args.hook_name, payload if isinstance(payload, dict) else {})
        return 0  # a hook never blocks or breaks a session, and prints nothing
    if args.command == "promote" and args.mode == "adapter":
        from . import promote
        return promote.main_adapter(describe=args.describe)
    if args.command == "prime":
        return prime.run(brief=args.brief, as_json=args.json, static=args.static, section=args.section)
    try:
        if args.command == "record":
            if not args.entity:
                ap.parse_args(["record", "--help"])
            entity = definitions.load()["entities"][args.entity]
            values = {f["name"]: getattr(args, f["name"]) for f in entity["fields"]}
            flags = {k: getattr(args, k) for k in (entity["sourceKind"].get("flags") or {})}
            if args.dry_run:
                out = memory.record(args.entity, values, supersede=args.supersede, flags=flags, dry_run=True)
            else:
                out = memory.record_or_queue(args.entity, values, supersede=args.supersede, flags=flags)
            return _emit(out, args.json)
        if args.command == "recall":
            if not args.entity:
                ap.parse_args(["recall", "--help"])
            entity = definitions.load()["entities"][args.entity]
            filters = {f: getattr(args, f, None) for r in entity["recall"] for f in r.get("filters", [])}
            out = memory.recall(args.entity, filters, history=getattr(args, "history", None))
            return _emit(out, args.json)
        if args.command == "topic":
            if args.topic_command != "add":
                ap.parse_args(["topic", "--help"])
            return _emit(memory.add_topic(args.name, args.alias), False)
        if args.command == "flush":
            return _emit(memory.flush_all(), False)
        if args.command == "note":
            from . import capture
            return _emit(capture.note(args.text, kind=args.kind, about=args.about, source=args.source), args.json)
        if args.command == "capture":
            from . import capture
            if args.capture_command == "quarantine":
                return _emit(capture.quarantine(clear=args.clear, requeue=args.requeue), args.json)
            if args.capture_command != "flush":
                ap.parse_args(["capture", "--help"])
            return _emit(capture.flush(), False)
        if args.command == "promote":
            from . import promote
            if args.event:
                return _emit(promote.receive_event(json.load(sys.stdin)), args.json)
            if args.pending:
                return _emit(promote.list_blocked(), args.json)
            return _emit(promote.run(session=args.session, retry_blocked=args.retry_blocked), args.json)
    except quipu.QuipuError as exc:
        print(f"camayoc: {exc}", file=sys.stderr)
        return 3
    except memory.BadInput:
        print("Not recorded: a value cannot be used in an IRI or query (spaces, brackets, quotes or "
              "control characters).", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - plain words, never a traceback
        print(f"camayoc: unexpected {type(exc).__name__}; nothing was written. Report it with the command you ran.",
              file=sys.stderr)
        return 3
    return 2


if __name__ == "__main__":
    sys.exit(main())
