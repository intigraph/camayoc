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

    sub.add_parser("flush", help="replay records queued while quipu was unreachable")
    sub.add_parser("mcp", help="serve the generated record_*/recall_* MCP tools on stdio")

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
    if args.command == "mcp":
        from . import mcp
        return mcp.serve()
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
            return _emit(memory.flush(), False)
    except quipu.QuipuError as exc:
        print(f"camayoc: {exc}", file=sys.stderr)
        return 3
    return 2


if __name__ == "__main__":
    sys.exit(main())
