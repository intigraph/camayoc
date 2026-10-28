"""Promotion: untyped captures -> typed camayoc entities (agent-memory-entities.md §5,
docs/design/memory-capture.md).

Only captures that carry a deterministic `aegis:kindHint` are candidates; an
ordinary prompt or answer stays evidence. For each candidate, oldest first:

1. Dedupe: the same contentHash captured within 30 days before it makes it
   `aegis:duplicateOf` the earlier capture.
2. Fill: a `cy record ...` line is parsed by cy's own parser; a DIRECTIVE /
   DECIDED / RULING marker (or a configured issuer prefix such as `Stiwi:`)
   is mapped to the entity's fields from the marker's `(key=value, ...)`
   parameters, the issuer map and the capture itself. No model is involved.
3. Record through memory.record(): the same shapes, natural keys, secret rules
   and supersession as a direct `cy record` call. Same key + same body is
   `unchanged`; same key + new body supersedes; nothing piles up.
4. Mark the capture: `aegis:promotedTo` on success, `aegis:promotionBlocked`
   (the plain-word refusal) plus `aegis:suggestedCall` when refused.

The optional extractor (OFF unless configured) only ever adds a
`suggestedCall` to a blocked capture. It never records, and it refuses to run
anything that names Claude or Anthropic: automation never spends Claude.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys

from . import capture, definitions, memory, planes, quipu

PROV = capture.PROV
RDFS = capture.RDFS
XSD = capture.XSD
DEDUPE_DAYS = 30
LIMIT = 50
PENDING, SETTLED = "PENDING", "SETTLED"
_PARAMS = re.compile(r"^\s*[\(\[]([^\)\]\n]{0,400})[\)\]]")
_FORBIDDEN = re.compile(r"claude|anthropic", re.I)


def _a() -> str:
    return memory.ns()


def _from() -> str:
    return f"FROM <{planes.plane_for('inferred')}>"


def _lit(value) -> str:
    return memory._sparql_text(str(value))


# ---- reads ---------------------------------------------------------------------------

def _rows(client: quipu.Client, sparql: str) -> list[dict]:
    return [{k: quipu.value(v) for k, v in r.items()} for r in client.query(sparql)]


def pending(client: quipu.Client, *, session: str | None = None, retry_blocked: bool = False,
            only: list[str] | None = None, limit: int = LIMIT, blocked_only: bool = False) -> list[dict]:
    if only:  # one bound-subject query per capture (no VALUES; cheap on quipu)
        out = []
        for iri in only:
            out += _pending(client, session=session, retry_blocked=retry_blocked, bind=iri, limit=1,
                            blocked_only=blocked_only)
        return out
    return _pending(client, session=session, retry_blocked=retry_blocked, limit=limit, blocked_only=blocked_only)


def _pending(client: quipu.Client, *, session: str | None, retry_blocked: bool, limit: int,
             blocked_only: bool, bind: str | None = None) -> list[dict]:
    a = _a()
    sess = ""
    if session:
        iri = session if session.startswith("urn:") else f"urn:camayoc:session:claude:{session}"
        sess = f"?c <{PROV}wasAssociatedWith> {memory.iri_ref(iri)} ."
    values = f"BIND({memory.iri_ref(bind)} AS ?c)" if bind else ""
    blocked = "" if retry_blocked or blocked_only else f"FILTER NOT EXISTS {{ ?c <{a}promotionBlocked> ?b }}"
    if blocked_only:
        blocked = f"?c <{a}promotionBlocked> ?blocked . OPTIONAL {{ ?c <{a}suggestedCall> ?suggested }}"
    return _rows(client, (
        f"SELECT ?c ?kind ?text ?hash ?at ?src ?agent ?about ?blocked ?suggested {_from()} WHERE {{ {values} "
        f"?c <{a}kindHint> ?kind ; <{a}captureSource> ?src ; <{a}contentHash> ?hash ; "
        f"<{a}capturedAt> ?at ; <{RDFS}comment> ?text . {sess} "
        f"OPTIONAL {{ ?c <{a}recordedBy> ?agent }} OPTIONAL {{ ?c <{a}about> ?about }} "
        f"FILTER NOT EXISTS {{ ?c <{a}promotedTo> ?p }} FILTER NOT EXISTS {{ ?c <{a}duplicateOf> ?d }} "
        f"{blocked} }} ORDER BY ?at ?c LIMIT {int(limit)}"))


def earlier_twin(client: quipu.Client, cap: dict) -> str | None:
    """The earliest other capture with the same content within DEDUPE_DAYS before it."""
    a = _a()
    rows = _rows(client, (
        f"SELECT ?o ?at {_from()} WHERE {{ ?o <{a}contentHash> '{_lit(cap['hash'])}' ; "
        f"<{a}capturedAt> ?at ; <{a}captureSource> ?s . FILTER(?o != {memory.iri_ref(cap['c'])}) }} "
        f"ORDER BY ?at ?o LIMIT 20"))
    mine = _parse(cap["at"])
    for r in rows:
        t = _parse(r["at"])
        if t is None or mine is None:
            continue
        if (t, r["o"]) < (mine, cap["c"]) and mine - t <= _dt.timedelta(days=DEDUPE_DAYS):
            return r["o"]
    return None


def _parse(text: str) -> _dt.datetime | None:
    try:
        return _dt.datetime.fromisoformat(text.split("^^")[0].strip('"').replace("Z", "+00:00"))
    except ValueError:
        return None


# ---- fill: capture text -> (entity, args, flags, supersede) ------------------------------

class Unfillable(ValueError):
    def __init__(self, lines: list[str], entity: str | None = None, partial: dict | None = None):
        super().__init__("; ".join(lines))
        self.lines, self.entity, self.partial = lines, entity, partial or {}


def _cy_line(text: str) -> list[str]:
    m = capture.CY_LINE.search(text)
    rest = text[m.start():]
    line, out = "", []
    for raw in rest.splitlines():
        if raw.rstrip().endswith("\\"):
            out.append(raw.rstrip()[:-1])
            continue
        out.append(raw)
        break
    line = " ".join(out).strip().lstrip("$").strip()
    try:
        argv = shlex.split(line)
    except ValueError as exc:
        raise Unfillable([f"Not promoted: the cy line does not parse ({exc})."]) from None
    return argv[1:]  # drop `cy` / `camayoc`


def fill_cy(text: str) -> tuple[str, dict, dict, str | None]:
    from . import cli
    argv = _cy_line(text)
    if len(argv) < 2 or argv[0] != "record" or argv[1] not in definitions.load()["entities"]:
        raise Unfillable([f"Not promoted: `{' '.join(argv[:2])}` is not a cy record call for a known entity."])
    parser = cli.build_parser()
    err = []

    def fail(message):
        err.append(message)
        raise SystemExit(2)
    for p in [parser, *_subparsers(parser)]:
        p.error = fail  # type: ignore[method-assign]
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        raise Unfillable([f"Not promoted: the cy line is not valid ({err[0] if err else 'usage'})."],
                         argv[1]) from None
    entity = definitions.load()["entities"][args.entity]
    values = {f["name"]: getattr(args, f["name"]) for f in entity["fields"]}
    flags = {k: getattr(args, k) for k in (entity["sourceKind"].get("flags") or {})}
    return args.entity, values, flags, args.supersede


def _subparsers(parser: argparse.ArgumentParser):
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for sub in action.choices.values():
                yield sub
                yield from _subparsers(sub)


def _marker_params(rest: str) -> tuple[dict, str]:
    m = _PARAMS.match(rest)
    if not m:
        return {}, rest
    params = {}
    for part in re.split(r"[,;]\s*|\s+(?=[A-Za-z-]+=)", m.group(1)):
        k, sep, v = part.partition("=")
        if sep and k.strip():
            params[k.strip().lower().lstrip("-")] = v.strip().strip("'\"")
    return params, rest[m.end():]


def fill_marker(cap: dict, config: dict) -> tuple[str, dict, dict, str | None]:
    text, kind = cap["text"], cap["kind"]
    entity = definitions.load()["entities"][kind]
    issuers = config.get("issuers") or {}
    issuer_iri = None
    m = capture.MARKER.search(text)
    if m:
        rest = text[m.end():]
    else:
        names = sorted(issuers, key=len, reverse=True)
        m = re.search(capture._ISSUER_PREFIX.format(names="|".join(re.escape(n) for n in names)), text) if names else None
        if not m:
            raise Unfillable([f"Not promoted: no {kind} marker found."], kind)
        issuer_iri = issuers[m.group(1)]
        rest = text[m.end():]
    params, rest = _marker_params(rest)
    body = re.split(r"\n\s*\n", rest.lstrip(" :\t-—").strip(), maxsplit=1)[0].strip()
    by_flag = {f["flag"].lstrip("-"): f["name"] for f in entity["fields"]}
    values: dict = {}
    unknown = [k for k in params if k not in by_flag]
    if unknown:
        raise Unfillable([f"Not promoted: unknown {kind} field(s) {', '.join(unknown)}; "
                          f"use {', '.join(sorted(by_flag))}."], kind)
    for k, v in params.items():
        values[by_flag[k]] = v
    if body and not values.get(entity["body"]):
        values[entity["body"]] = body
    issuer_field = entity.get("issuer")
    if issuer_field and not values.get(issuer_field):
        if issuer_iri:
            values[issuer_field] = issuer_iri
        elif cap.get("src") == "prompt" and config.get("prompt_author"):
            values[issuer_field] = config["prompt_author"]
    names = {f["name"] for f in entity["fields"]}
    if "verbatim" in names and not values.get("verbatim"):
        values["verbatim"] = memory._CONTROL.sub(" ", text.strip())[:1000]
    if "issuedOn" in names and not values.get("issuedOn"):
        values["issuedOn"] = cap["at"][:10]
    if "about" in names and not values.get("about") and cap.get("about"):
        values["about"] = cap["about"]
    return kind, values, {}, None


def fill(cap: dict, config: dict) -> tuple[str, dict, dict, str | None]:
    kind = cap.get("kind") or ""
    if kind.startswith("cy:"):
        return fill_cy(cap["text"])
    if kind in ("directive", "decision"):
        return fill_marker(cap, config)
    raise Unfillable([f"Not promoted: no deterministic rule for kind hint '{kind}'."])


def suggested_call(entity_name: str, values: dict) -> str:
    entity = definitions.load()["entities"][entity_name]
    parts = ["cy", "record", entity_name]
    for f in entity["fields"]:
        vals = memory._values(values.get(f["name"]))
        if f["name"] == "verbatim":
            continue
        if not vals and f["required"]:
            parts += [f["flag"], f"<{f['name']}>"]
        for v in vals:
            parts += [f["flag"], shlex.quote(v) if not v.startswith("<") else v]
    return " ".join(parts)


# ---- the optional extractor (OFF by default; never Claude) --------------------------------

def extractor_argv(config: dict, env=None) -> list[str] | None:
    env = os.environ if env is None else env
    argv = (config.get("extractor") or {}).get("argv") if isinstance(config.get("extractor"), dict) else None
    if not argv or env.get("CAMAYOC_PROMOTE_EXTRACT", "1") == "0":
        return None
    if not isinstance(argv, list) or not all(isinstance(x, str) and x for x in argv):
        return None
    if any(_FORBIDDEN.search(x) for x in argv):
        raise ValueError("the promotion extractor may not run Claude or Anthropic models "
                         "(automation never spends Claude); configure a codex or local model")
    return argv


def extract(argv: list[str], cap: dict, problems: list[str], partial: dict, env=None) -> str | None:
    """Ask the configured extractor for a suggested cy call. Its answer is a suggestion only."""
    env = dict(os.environ if env is None else env)
    for key in list(env):
        if key.startswith(("ANTHROPIC_", "CLAUDE")):
            env.pop(key)
    request = {"capture": cap["c"], "kind": cap.get("kind"), "text": cap["text"], "missing": problems,
               "partial": partial, "entities": sorted(definitions.load()["entities"])}
    try:
        done = subprocess.run(argv, input=json.dumps(request), capture_output=True, text=True, timeout=120,
                              env=env, check=False)
        doc = json.loads(done.stdout) if done.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("entity") not in definitions.load()["entities"]:
        return None
    args = doc.get("args") if isinstance(doc.get("args"), dict) else {}
    return suggested_call(doc["entity"], args) + "  # suggested by extractor (inferred), confirm before running"


# ---- writes ---------------------------------------------------------------------------------

def _mark(client: quipu.Client, cap: str, triples: list[str], env=None) -> None:
    body = f"{memory.iri_ref(cap)}\n    " + " ;\n    ".join(triples) + " .\n"
    client.knot(body, planes.plane_for("inferred"), memory.agent_name(env), "camayoc:cy promote")


def _stamp() -> str:
    return f'"{memory.iso(memory.now())}"^^<{XSD}dateTime>'


def promote_one(client: quipu.Client, cap: dict, config: dict, env=None) -> dict:
    a = _a()
    twin = earlier_twin(client, cap)
    if twin:
        _mark(client, cap["c"], [f"<{a}duplicateOf> {memory.iri_ref(twin)}",
                                 f"<{a}promotionOutcome> {memory.lit('duplicate')}"], env)
        return {"capture": cap["c"], "status": "duplicate", "of": twin}
    try:
        entity_name, values, flags, supersede = fill(cap, config)
    except Unfillable as exc:
        entity_name, values, flags, supersede, problems = exc.entity, exc.partial, {}, None, exc.lines
    else:
        out = memory.record(entity_name, values, supersede=supersede, flags=flags, client=client, env=env,
                            derived_from=cap["c"], source="camayoc:cy promote")
        if out.code == 0:
            iri = out.data["iri"]
            _mark(client, cap["c"], [f"<{a}promotedTo> {memory.iri_ref(iri)}",
                                     f"<{a}promotionOutcome> {memory.lit(out.data['status'])}",
                                     f"<{a}promotedAt> {_stamp()}"], env)
            return {"capture": cap["c"], "status": out.data["status"], "iri": iri, "entity": entity_name,
                    "lines": out.lines}
        if out.code != 2:  # partial write (supersession back-link failed): retry later, no mark
            return {"capture": cap["c"], "status": "retry", "lines": out.lines}
        problems = [ln for ln in out.lines if not ln.startswith("Example:")]
    suggestion = suggested_call(entity_name, values) if entity_name else None
    argv = extractor_argv(config, env)
    if argv and (suggestion is None or "<" in suggestion):
        suggestion = extract(argv, cap, problems, values, env) or suggestion
    triples = [f"<{a}promotionBlocked> {memory.lit(' '.join(problems))}",
               f"<{a}promotionOutcome> {memory.lit('blocked')}", f"<{a}promotedAt> {_stamp()}"]
    if suggestion:
        triples.append(f"<{a}suggestedCall> {memory.lit(suggestion)}")
    _mark(client, cap["c"], triples, env)
    return {"capture": cap["c"], "status": "blocked", "problems": problems, "suggested": suggestion}


def run(*, session: str | None = None, retry_blocked: bool = False, only: list[str] | None = None,
        client: quipu.Client | None = None, env=None, limit: int = LIMIT) -> memory.Outcome:
    """Promote pending candidates, oldest first. Idempotent: a promoted,
    duplicate or blocked capture is never pending again (blocked ones come
    back only with retry_blocked)."""
    client = client or capture.client_for(env)
    config = capture.load_config(env)
    results, lines = [], []
    try:
        for cap in pending(client, session=session, retry_blocked=retry_blocked, only=only, limit=limit):
            r = promote_one(client, cap, config, env)
            results.append(r)
            if r["status"] in ("recorded", "superseded", "unchanged"):
                lines.append(f"promoted {r['capture']} -> {r['iri']} ({r['status']})")
            elif r["status"] == "duplicate":
                lines.append(f"duplicate {r['capture']} of {r['of']}")
            elif r["status"] == "blocked":
                lines.append(f"blocked {r['capture']}: {' '.join(r['problems'])}")
                if r.get("suggested"):
                    lines.append(f"  finish with: {r['suggested']}")
            else:
                lines.append(f"retry {r['capture']}: {' '.join(r.get('lines', []))}")
    except quipu.Unreachable as exc:
        lines.append(f"stopped: {exc}")
        return memory.Outcome(1, lines, {"results": results})
    if not results:
        lines.append("nothing to promote")
    return memory.Outcome(0, lines, {"results": results})


def list_blocked(client: quipu.Client | None = None) -> memory.Outcome:
    client = client or quipu.Client()
    rows = pending(client, blocked_only=True, limit=200)
    lines = []
    for r in rows:
        lines.append(f"{r['c']} [{r['kind']}] {r.get('blocked', '')}")
        if r.get("suggested"):
            lines.append(f"  finish with: {r['suggested']}")
    return memory.Outcome(0, lines or ["no blocked captures"], {"rows": rows})


# ---- chaski: incremental adapter + command-sink receiver ------------------------------------

def _event_id(item: str) -> str:
    return "memory-capture-pending:" + hashlib.sha256(item.encode()).hexdigest()[:24]


def _is_capture(iri) -> bool:
    return isinstance(iri, str) and iri.startswith(memory.ns() + "mem/capture/") and memory._IRI.match(iri)


def adapter(describe: bool, request: dict | None, client: quipu.Client | None = None) -> dict:
    """chaski's version-1 incremental protocol (chaski docs/installation-reference.md)."""
    a = _a()
    if describe:
        return {"version": 1, "graphs": [planes.plane_for("inferred")], "types": [],
                "attributes": [a + p for p in ("kindHint", "promotedTo", "promotionBlocked", "duplicateOf")]}
    request = request or {}
    client = client or quipu.Client()
    if request.get("discover"):
        return {"version": 1, "items": [r["c"] for r in pending(client, limit=10000)]}
    if "route" in request:
        items = sorted({r.get("entity") for r in request["route"] if isinstance(r, dict) and _is_capture(r.get("entity"))})
        return {"version": 1, "items": items}
    items = [i for i in request.get("items", []) if _is_capture(i)]
    open_ = {r["c"] for r in pending(client, only=items, limit=len(items) or 1)} if items else set()
    records = [{"capture": i, "verdict": PENDING if i in open_ else SETTLED, "event_id": _event_id(i),
                "evidence": "unpromoted capture with a kind hint" if i in open_ else "promoted, duplicate or blocked"}
               for i in items]
    return {"version": 1, "scope": list(request.get("items", [])), "records": records,
            "next_checks": {r["capture"]: None for r in records}}


def receive_event(event: dict, client: quipu.Client | None = None, env=None) -> memory.Outcome:
    """Command-sink receiver: promote the one capture the event names. Exit 0 is
    the receipt; idempotent on the event because a settled capture is no
    longer pending."""
    item = event.get("item")
    if not _is_capture(item):
        return memory.Outcome(0, ["event names no capture; acknowledged"])
    out = run(only=[item], client=client, env=env, limit=1)
    return out


def main_adapter(describe: bool) -> int:
    request = None if describe else json.load(sys.stdin)
    print(json.dumps(adapter(describe, request), sort_keys=True))
    return 0
