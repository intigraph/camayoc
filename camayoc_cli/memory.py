"""Record and recall agent-memory entities through Quipu.

docs/design/agent-memory-entities.md §2-§3. Every rule here reads the
generated definitions (data/definitions.json); no field is named in code.
The order of checks is the order of the §3 behavior table: missing fields,
secret rules, unknown key parts, then supersession guards, then the write,
which Quipu's shapes gate again.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
from dataclasses import dataclass, field

from . import definitions, planes, quipu
from .offline import queue_path, queue_length, enqueue
from .secret_rules import refusals as secret_refusals

CREW = "crew"
TOPIC_NS = "mem/topic/"


class BadInput(ValueError):
    """A caller value that cannot become an IRI or a query parameter."""


_IRI = re.compile(r"^(?:[a-z][a-z0-9+.-]*://|urn:)[^\s<>\"{}|\\^`\x00-\x1f\x7f]+$", re.I)
_LOCAL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,199}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


@dataclass
class Outcome:
    """What a record/recall call did. `code` is the process exit status."""
    code: int
    lines: list[str] = field(default_factory=list)
    data: dict = field(default_factory=dict)


def ns() -> str:
    return definitions.load()["namespace"]


def now() -> _dt.datetime:
    fixed = os.environ.get("CAMAYOC_NOW")  # tests pin the clock
    if fixed:
        return _dt.datetime.fromisoformat(fixed.replace("Z", "+00:00")).astimezone(_dt.timezone.utc)
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)


def iso(t: _dt.datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def local_name(iri: str) -> str:
    return re.split(r"[/#]", iri.rstrip("/"))[-1]


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")


def to_iri(value) -> str:
    """'crew', a full IRI, an aegis: CURIE or a bare local name -> IRI.

    Raises BadInput for anything else (spaces, brackets, quotes, control
    characters), so no caller value ever reaches Turtle or SPARQL unchecked.
    """
    value = str(value).strip()
    if value == CREW:
        return ns() + CREW
    if re.match(r"^[a-z][a-z0-9+.-]*://", value, re.I) or value.startswith("urn:"):
        if not _IRI.match(value):
            raise BadInput(value)
        return value
    prefix, sep, rest = value.partition(":")
    prefixes = definitions.load()["prefixes"]
    if sep and prefix in prefixes:
        if not _LOCAL.match(rest):
            raise BadInput(value)
        return prefixes[prefix] + rest
    if not _LOCAL.match(value):
        raise BadInput(value)
    return ns() + value


def _shown(value) -> str:
    text = _CONTROL.sub("?", str(value))
    return repr(text if len(text) <= 60 else text[:57] + "...")


def agent_name(env=None) -> str:
    env = os.environ if env is None else env
    for key in ("SHANTY_AGENT", "QUIPU_AGENT"):
        if env.get(key):
            return env[key]
    if env.get("CLAUDECODE") == "1":
        return "claude-code"
    if env.get("CODEX_HOME"):
        return "codex"
    return "user:" + (env.get("USER") or "unknown")


def session_iri(env=None) -> str | None:
    env = os.environ if env is None else env
    for key, harness in (("CLAUDE_CODE_SESSION_ID", "claude"), ("CODEX_SESSION_ID", "codex"),
                         ("CODEX_THREAD_ID", "codex")):
        if env.get(key) and re.fullmatch(r"[A-Za-z0-9._-]{1,128}", env[key]):
            return f"urn:camayoc:session:{harness}:{env[key]}"
    return None


def lit(text: str) -> str:
    escaped = (text.replace("\\", "\\\\").replace('"', '\\"')
               .replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t"))
    return f'"{escaped}"'


def iri_ref(iri: str) -> str:
    if re.search(r'[\s<>"{}|\\^`\x00-\x1f\x7f]', iri):
        raise BadInput(iri)
    return f"<{iri}>"


def _sparql_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("'", "\\'")


def _from_all() -> str:
    graphs = [quipu.ROOT_GRAPH, *planes.iris().values()]
    return " ".join(f"FROM <{g}>" for g in graphs)


# ---- argument normalization -------------------------------------------------

def _values(raw) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return [str(v) for v in raw if v is not None and str(v) != ""]
    return [] if str(raw) == "" else [str(raw)]


def precheck(entity: dict, args: dict) -> list[str]:
    """Shape rules cy can check with no store: presence, counts, enums, patterns, secrets."""
    out = []
    for f in entity["fields"]:
        vals = _values(args.get(f["name"]))
        if f["required"] and not vals:
            out.append(f["message"])
            continue
        if not vals:
            continue
        if any(_CONTROL.search(v) for v in vals):
            out.append(f"Not recorded: {f['flag']} contains control characters.")
            continue
        if f["kind"] == "iri" and (f["name"] != "topic" or any(":" in v for v in vals)):
            bad = []
            for v in vals:
                try:
                    to_iri(v)
                except BadInput:
                    bad.append(v)
            if bad:
                what = "a topic name or IRI" if f["name"] == "topic" else "an entity IRI or local name"
                out.append(f"Not recorded: {f['flag']} {_shown(bad[0])} is not {what}.")
                continue
        if f["maxCount"] is not None and len(vals) > f["maxCount"]:
            out.append(f["message"])
        elif f["enum"] and any(v not in f["enum"] for v in vals):
            out.append(f["message"])
        elif f["pattern"] and f["name"] != "secretRef" and any(not re.search(f["pattern"], v) for v in vals):
            out.append(f["message"])
    flat = {f["name"]: " ".join(_values(args.get(f["name"]))) or None for f in entity["fields"]}
    for line in secret_refusals(flat):
        if line not in out:
            out.append(line)
    return out


def example_line(entity: dict) -> str:
    parts = [f"cy record {entity['noun']}"]
    flags = {f["name"]: f["flag"] for f in entity["fields"]}
    for name in [f["name"] for f in entity["fields"] if f["name"] in entity["example"]]:
        for v in _values(entity["example"][name]):
            parts.append(f"{flags[name]} {json.dumps(v)}" if " " in v or '"' in v else f"{flags[name]} {v}")
    return "Example: " + " ".join(parts)


# ---- store lookups ------------------------------------------------------------

def entity_exists(client: quipu.Client, iri: str) -> bool:
    rows = client.query(f"SELECT ?p {_from_all()} WHERE {{ {iri_ref(iri)} ?p ?o }} LIMIT 1")
    return bool(rows)


def nearest(client: quipu.Client, name: str) -> list[str]:
    return [local_name(c["iri"]) for c in client.resolve(name, 3) if c.get("iri")][:3]


def find_topic(client: quipu.Client, value: str) -> str | None:
    """A registered MemoryTopic by exact label or alias, or by its IRI."""
    a = ns()
    if value.startswith(("http://", "https://", "urn:")) or ":" in value:
        iri = to_iri(value)
        rows = client.query(f"SELECT ?t {_from_all()} WHERE {{ BIND({iri_ref(iri)} AS ?t) "
                            f"?t a <{a}MemoryTopic> }} LIMIT 1")
        return iri if rows else None
    text = _sparql_text(value)
    rows = client.query(
        f"SELECT ?t {_from_all()} WHERE {{ ?t a <{a}MemoryTopic> . "
        f"{{ ?t <http://www.w3.org/2000/01/rdf-schema#label> ?n }} UNION {{ ?t <{a}alias> ?n }} "
        f"FILTER(LCASE(STR(?n)) = LCASE('{text}')) }} ORDER BY ?t LIMIT 1")
    return quipu.value(rows[0]["t"]) if rows else None


def near_topics(client: quipu.Client, value: str) -> list[str]:
    a = ns()
    word = _sparql_text(slug(value).split("-")[0] if slug(value) else value)
    rows = client.query(
        f"SELECT DISTINCT ?n {_from_all()} WHERE {{ ?t a <{a}MemoryTopic> ; "
        f"<http://www.w3.org/2000/01/rdf-schema#label> ?n . "
        f"FILTER(CONTAINS(LCASE(STR(?n)), LCASE('{word}'))) }} ORDER BY ?n LIMIT 3")
    return [quipu.value(r["n"]) for r in rows]


def is_person(client: quipu.Client, iri: str) -> bool:
    return bool(client.query(f"SELECT ?x {_from_all()} WHERE {{ BIND({iri_ref(iri)} AS ?x) "
                             f"?x a <{ns()}Person> }} LIMIT 1"))


def current_versions(client: quipu.Client, entity: dict, key: str) -> list[dict]:
    a = ns()
    issuer = next((f for f in entity["fields"] if f["name"] == entity.get("issuer")), None)
    issuer_sel = f"OPTIONAL {{ ?iri <{definitions.expand(issuer['path'])}> ?issuer }}" if issuer else ""
    rows = client.query(
        f"SELECT ?iri ?hash ?sourceKind ?issuer {_from_all()} WHERE {{ "
        f"?iri <{a}naturalKey> {iri_ref(key)} ; <{a}contentHash> ?hash ; <{a}sourceKind> ?sourceKind . "
        f"{issuer_sel} FILTER NOT EXISTS {{ ?iri <{a}supersededBy> ?newer }} }} ORDER BY DESC(?iri)")
    return [{k: quipu.value(v) for k, v in r.items()} for r in rows]


def supersession_turtle(old: str, new: str) -> str:
    """The back-link, stamped with when it was written (wall clock, not the new
    version's recordedAt), so a late repair still lands after bobbin's cursor."""
    stamp = iso(_dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)) if not os.environ.get("CAMAYOC_NOW") \
        else iso(now())
    return (f"{iri_ref(old)} <{ns()}supersededBy> {iri_ref(new)} ;\n"
            f'    <{ns()}supersededAt> "{stamp}"^^<http://www.w3.org/2001/XMLSchema#dateTime> .\n')


def repair(client: quipu.Client, current: list[dict], env=None) -> list[str]:
    """Mark every current version but the newest as superseded by the newest."""
    if len(current) < 2:
        return []
    newest, lines = current[0]["iri"], []
    for stale in current[1:]:
        back = supersession_turtle(stale["iri"], newest)
        client.knot(back, planes.plane_for(stale["sourceKind"]), agent_name(env), "camayoc:cy repair supersession")
        lines.append(f"repaired: {stale['iri']} marked superseded by {newest}")
    return lines


# ---- record ---------------------------------------------------------------------

def record(entity_name: str, args: dict, *, supersede: str | None = None, flags: dict | None = None,
           dry_run: bool = False, client: quipu.Client | None = None, env=None,
           derived_from: str | None = None, source: str | None = None) -> Outcome:
    defs = definitions.load()
    entity = defs["entities"][entity_name]
    flags = flags or {}
    problems = precheck(entity, args)
    if problems:
        return Outcome(2, problems + [example_line(entity)])
    client = client or quipu.Client()
    by_name = {f["name"]: f for f in entity["fields"]}

    # Resolve IRI-valued fields and topics (§2.1). Unknown key parts refuse.
    resolved: dict[str, list[str]] = {}
    unknown = []
    try:
        for f in entity["fields"]:
            vals = _values(args.get(f["name"]))
            if not vals:
                continue
            if f["kind"] != "iri":
                resolved[f["name"]] = vals
                continue
            out = []
            for v in vals:
                if f["name"] == "topic":
                    try:
                        t = find_topic(client, v)
                    except BadInput:
                        unknown.append(f"Not recorded: {f['flag']} {_shown(v)} is not a topic name or IRI.")
                        continue
                    if t is None:
                        near = near_topics(client, v)
                        hint = f" Nearest: {', '.join(near)}." if near else ""
                        unknown.append(f"Not recorded: topic '{v}' is not registered.{hint} "
                                       f"Register it with: cy topic add {slug(v) or v}")
                        continue
                    out.append(t)
                    continue
                try:
                    iri = to_iri(v)
                except BadInput:
                    unknown.append(f"Not recorded: {f['flag']} {_shown(v)} is not an entity IRI or local name.")
                    continue
                if iri != ns() + CREW and not entity_exists(client, iri):
                    near = nearest(client, local_name(iri))
                    hint = f" Nearest: {', '.join(near)}." if near else ""
                    unknown.append(f"Not recorded: {f['flag']} '{v}' is not an entity in quipu.{hint}")
                    continue
                out.append(iri)
            resolved[f["name"]] = out
        if unknown:
            return Outcome(2, unknown)

        # sourceKind by rule.
        rule = entity["sourceKind"]
        source_kind = rule["default"]
        when = rule.get("declaredWhen")
        if when and resolved.get(when["field"]) and is_person(client, resolved[when["field"]][0]):
            source_kind = "declared"
        for flag, spec in (rule.get("flags") or {}).items():
            if flags.get(flag):
                source_kind = spec["sourceKind"]

        a = ns()
        key_parts = [local_name(resolved[k][0]) for k in entity["key"]]
        key = f"{a}mem/{entity_name}/" + "/".join(key_parts)
        t = now()
        version = f"{key}/{t.strftime('%Y%m%dT%H%M%SZ')}"
        canonical = json.dumps({k: resolved[k] for k in sorted(resolved)}, sort_keys=True, ensure_ascii=False)
        content_hash = "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()

        current = current_versions(client, entity, key)
        old = current[0] if current else None
        # Repair first: more than one current version on a key means an earlier
        # supersededBy back-link never landed (review of #183). Every older one
        # is marked superseded by the newest before anything else happens, so a
        # re-run finishes the job instead of reporting "unchanged" over two
        # current records.
        repaired = repair(client, current, env)
        if old and old["hash"] == content_hash:
            return Outcome(0, repaired + [f"unchanged {old['iri']}"], {"iri": old["iri"], "status": "unchanged"})
        if old:
            if old["sourceKind"] == "declared" and source_kind == "inferred":
                return Outcome(2, [f"Not recorded: an inferred record never overrides a human-declared one "
                                   f"({old['iri']}); ask the person, then record it as declared."])
            issuer = entity.get("issuer")
            if (issuer and old["sourceKind"] == "declared" and source_kind == "declared"
                    and old.get("issuer") and resolved.get(issuer)
                    and old["issuer"] != resolved[issuer][0] and supersede != old["iri"]):
                return Outcome(2, [f"Not recorded: this replaces a ruling by {local_name(old['issuer'])} "
                                   f"({old['iri']}); pass --supersede {old['iri']} to confirm."])
        if old and version == old["iri"]:
            return Outcome(2, ["Not recorded: a version with this timestamp exists; retry in a second."])

        # Build the record.
        body = " ".join(resolved.get(entity["body"], [])) if by_name[entity["body"]]["kind"] != "iri" else ""
        label = f"{entity_name.capitalize()}: {body}".strip()
        if len(label) > 160:
            label = label[:157] + "..."
        triples = [f"a <{definitions.expand(entity['class'])}>",
                   f"<{a}naturalKey> {iri_ref(key)}",
                   f"<http://www.w3.org/2000/01/rdf-schema#label> {lit(label)}",
                   f"<{a}sourceKind> {lit(source_kind)}",
                   f'<{a}recordedAt> "{iso(t)}"^^<http://www.w3.org/2001/XMLSchema#dateTime>',
                   f"<{a}recordedBy> {lit(agent_name(env))}",
                   f"<{a}contentHash> {lit(content_hash)}"]
        session = session_iri(env)
        if session:
            triples.append(f"<http://www.w3.org/ns/prov#wasAssociatedWith> {iri_ref(session)}")
        life = entity.get("lifecycle", {})
        if life.get("reviewAfterDays"):
            due = (t + _dt.timedelta(days=life["reviewAfterDays"])).strftime("%Y-%m-%d")
            triples.append(f"<{a}reviewAfter> {lit(due)}")
        if life.get("maxAge"):
            triples.append(f"<{a}maxAge> {lit(life['maxAge'])}")
        if old:
            triples.append(f"<{a}supersedes> {iri_ref(old['iri'])}")
        if derived_from:  # promotion (§5 step 4): the capture this record came from
            triples.append(f"<http://www.w3.org/ns/prov#wasDerivedFrom> {iri_ref(derived_from)}")
        for name in sorted(resolved, key=lambda n: [f["name"] for f in entity["fields"]].index(n)):
            f = by_name[name]
            pred = f"<{definitions.expand(f['path'])}>"
            objs = [iri_ref(v) if f["kind"] == "iri" else lit(v) for v in resolved[name]]
            triples.append(f"{pred} {', '.join(objs)}")
        turtle = f"{iri_ref(version)}\n    " + " ;\n    ".join(triples) + " .\n"
        graph = planes.plane_for(source_kind)
        if dry_run:
            return Outcome(0, [turtle.rstrip(), f"# would write to {graph}"],
                           {"iri": version, "status": "dry-run", "graph": graph, "turtle": turtle})

        actor = agent_name(env)
        source = source or f"camayoc:cy record {entity_name}"
        client.knot(turtle, graph, actor, source)
        if old:
            back = supersession_turtle(old["iri"], version)
            try:
                client.knot(back, planes.plane_for(old["sourceKind"]), actor, source)
            except quipu.QuipuError as exc:
                return Outcome(1, [f"recorded {entity_name} {version}, but marking {old['iri']} superseded "
                                   f"failed: {exc}. Re-run the same command to finish."])
            return Outcome(0, repaired + [f"superseded {old['iri']} -> {version}"],
                           {"iri": version, "status": "superseded", "supersedes": old["iri"]})
        return Outcome(0, repaired + [f"recorded {entity_name} {version}"], {"iri": version, "status": "recorded"})
    except BadInput as exc:
        return Outcome(2, [f"Not recorded: {_shown(exc.args[0] if exc.args else '')} cannot be used in an IRI."])
    except quipu.Refused as exc:
        lines = list(exc.messages)
        paths = {str(i.get("path", "")) for i in exc.issues}
        if any(p.endswith(("/evidenceHash", "/outcome")) for p in paths):
            lines.append("This store still applies quipu's governance Decision shape to aegis:Decision; "
                         "record_decision needs the GateApproval migration (design §1.3).")
        return Outcome(2, lines)


def _queued_context(env=None):
    env = os.environ if env is None else env
    # Credentials never enter the buffer. Replay uses current credentials but
    # preserves the origin actor/session and refuses an endpoint/plane change.
    keys = ('SHANTY_AGENT', 'QUIPU_AGENT', 'CLAUDE_CODE_SESSION_ID', 'CODEX_SESSION_ID',
            'CODEX_THREAD_ID', 'QUIPU_SESSION', 'QUIPU_HARNESS', 'QUIPU_MODEL',
            'SHANTY_MODEL', 'CLAUDECODE', 'CODEX_HOME', 'USER')
    return dict(server=env.get('QUIPU_SERVER', quipu.DEFAULT_SERVER).rstrip('/'),
                plane_ns=env.get('CAMAYOC_PLANE_NS', 'https://camayoc.local/plane/'),
                env={key: env[key] for key in keys if env.get(key)})


def record_or_queue(entity_name: str, args: dict, *, supersede: str | None = None,
                    flags: dict | None = None, env=None) -> Outcome:
    """Local prechecks precede both writes and buffering. Never leapfrog a FIFO."""
    entity = definitions.load()["entities"][entity_name]
    problems = precheck(entity, args)
    problems.extend(secret_refusals({"supersede": supersede}) if supersede else [])
    if problems:
        return Outcome(2, problems + [example_line(entity)])
    count = queue_length(env)
    if count is None:
        return Outcome(3, ['Not recorded: offline queue is unreadable; inspect it before retrying.'])
    if not count:
        try:
            return record(entity_name, args, supersede=supersede, flags=flags, env=env)
        except quipu.Unreachable:
            pass
    context = _queued_context(env)
    n = enqueue({"entity": entity_name, "args": args, "supersede": supersede,
                 "flags": flags or {}, "context": context}, env)
    return Outcome(0, [f"queued {n}; run `cy flush` once quipu is back"],
                   {"status": "queued", "queued": n})


# ---- offline queue -------------------------------------------------------------



def flush(client: quipu.Client | None = None, env=None, *, limit=100, timeout=10.0) -> Outcome:
    from . import offline
    import time
    path = queue_path(env)
    if not path.exists() and not path.is_symlink():
        return Outcome(0, ["queue empty"])
    lines, done, local_error = [], 0, False
    deadline = time.monotonic() + timeout
    try:
        with offline.locked(path, replay=True):
            while done < limit and time.monotonic() < deadline:
                call = offline.head(path)
                if call is None:
                    break
                context = call.get('context')
                replay_env = {}  # legacy entries have no trustworthy origin metadata
                if context:
                    current = _queued_context(env)
                    if (context.get('server') != current['server']
                            or context.get('plane_ns') != current['plane_ns']):
                        lines.append('stopped: queued endpoint or plane differs; restore the original configuration')
                        break
                    replay_env = dict(context.get('env', {}))
                actual_client = client or quipu.Client(base=_queued_context(env)['server'],
                                                       timeout=timeout, deadline=deadline,
                                                       provenance_env=replay_env)
                try:
                    out = record(call['entity'], call['args'], supersede=call.get('supersede'),
                                 flags=call.get('flags'), client=actual_client, env=replay_env)
                except quipu.QuipuError as exc:
                    lines.append(f'stopped: {type(exc).__name__}; head retained')
                    break
                lines.extend(out.lines)
                if out.code != 0 or out.data.get('status') in ('queued', 'dry-run'):
                    lines.append('stopped: head was not confirmed; retained for inspection')
                    break
                offline.acknowledge(path, call)
                done += 1
    except (OSError, ValueError, KeyError, TypeError) as exc:
        local_error = True
        lines.append(f'stopped: local buffer {type(exc).__name__}; inspect acknowledgment state')
    rest = queue_length(env)
    lines.append(f"replayed {done}, {rest if rest is not None else 'UNKNOWN'} still queued")
    ok = rest == 0 and not local_error
    return Outcome(0 if ok else 1, lines,
                   dict(replayed=done, queued=rest, status='ok' if ok else 'pending'))


def flush_all(env=None, *, limit=100, timeout=10.0) -> Outcome:
    """Both producer FIFOs; capture support is activated by its explicit adapter."""
    from . import offline
    import time
    deadline = time.monotonic() + timeout
    # Archive retention must run even if the typed FIFO cannot replay or the
    # capture queue is empty. This is a worker/operator path, never hook append.
    try:
        from . import capture, capture_spool
        retention = capture_spool.prune(capture.spool_path(env), iso(capture._now(env)))
    except (ImportError, OSError, ValueError, TypeError, KeyError):
        retention = None
    out = flush(env=env, limit=limit, timeout=max(0.0, deadline - time.monotonic()))
    out.data['quarantine_retention'] = retention
    if retention is None:
        out.code = 1
        out.lines.append('quarantine maintenance UNKNOWN; inspect local state before retrying')
        return out
    if retention['expired'] or retention['capacity']:
        out.lines.append(f"quarantine retention discarded {retention['expired']} expired and {retention['capacity']} oldest archives")
    if out.code:
        return out
    if offline.capture_length(env) == 0:
        count = capture.quarantine(env).data['quarantined']
        out.data['quarantined_captures'] = count
        if count is None or count > 0:
            out.code = 1
            out.lines.append(f'{count if count is not None else "UNKNOWN"} quarantined captures; inspect cy capture quarantine')
        return out
    if not getattr(capture, 'OFFLINE_FIFO_V1', False):
        return Outcome(1, out.lines + ['capture backlog pending; capture replay adapter is not installed'])
    remaining = deadline - time.monotonic()
    remaining_limit = limit - out.data.get('replayed', 0)
    if remaining <= 0 or remaining_limit <= 0:
        return Outcome(1, out.lines + ['capture backlog pending; replay budget exhausted'])
    client = capture.client_for(env, timeout=remaining)
    client.deadline = deadline
    captured = capture.flush(client=client, env=env, limit=remaining_limit, deadline=deadline)
    data = offline.backlog(env)
    extra = captured.data.get('quarantine_retention', {})
    data['quarantine_retention'] = {key: retention.get(key, 0) + extra.get(key, 0)
                                   for key in retention.keys() | extra.keys()}
    return Outcome(captured.code, out.lines + captured.lines, data)


# ---- topics ---------------------------------------------------------------------

def add_topic(name: str, aliases: list[str], client: quipu.Client | None = None, env=None) -> Outcome:
    s = slug(str(name))
    if _CONTROL.search(str(name)) or any(_CONTROL.search(str(a)) for a in aliases):
        return Outcome(2, ["Not recorded: a topic name or alias contains control characters."])
    if not s:
        return Outcome(2, ["Not recorded: a topic needs a name of letters or digits (cy topic add <name>)."])
    problems = secret_refusals({"topic": name, **{f"alias{i}": a for i, a in enumerate(aliases)}})
    if problems:
        return Outcome(2, problems)
    client = client or quipu.Client()
    a = ns()
    iri = f"{a}{TOPIC_NS}{s}"
    if find_topic(client, s) == iri and not aliases:
        return Outcome(0, [f"unchanged {iri}"], {"iri": iri, "status": "unchanged"})
    triples = [f"a <{a}MemoryTopic>", f"<http://www.w3.org/2000/01/rdf-schema#label> {lit(s)}"]
    alias_vals = [x for x in aliases if slug(x) and x != s]
    if alias_vals:
        triples.append(f"<{a}alias> " + ", ".join(lit(x) for x in alias_vals))
    turtle = f"{iri_ref(iri)} " + " ;\n    ".join(triples) + " .\n"
    try:
        client.knot(turtle, planes.plane_for("declared"), agent_name(env), "camayoc:cy topic add")
    except quipu.Refused as exc:
        return Outcome(2, exc.messages)
    return Outcome(0, [f"registered topic {iri}"], {"iri": iri, "status": "recorded"})


# ---- recall ---------------------------------------------------------------------

def render(template: str, params: list[dict], values: dict) -> str:
    """Client-side render with quipu's own escaping, for stores without the stored query."""
    sparql = template
    for p in params:
        raw = str(values[p["name"]])
        if p["type"] == "iri":
            if not raw or re.search(r'[\s<>"{}|\\^`]', raw):
                raise ValueError(f"param '{p['name']}': invalid IRI")
            rendered = raw
        else:
            rendered = raw.replace("\\", "\\\\").replace("'", "\\'")
        sparql = sparql.replace("{" + p["name"] + "}", rendered)
    return sparql


def recall(entity_name: str, filters: dict, *, history: str | None = None,
           client: quipu.Client | None = None) -> Outcome:
    try:
        return _recall(entity_name, filters, history=history, client=client)
    except BadInput as exc:
        return Outcome(2, [f"Not recalled: {_shown(exc.args[0] if exc.args else '')} is not an entity IRI, "
                           "local name or key path."])


def _recall(entity_name: str, filters: dict, *, history: str | None = None,
            client: quipu.Client | None = None) -> Outcome:
    defs = definitions.load()
    entity = defs["entities"][entity_name]
    queries = entity["recall"]
    query = next((q for q in queries if bool(q.get("history")) == bool(history)), None)
    if query is None:
        return Outcome(2, [f"Not recalled: {entity_name} has no {'history' if history else 'current'} query."])
    t = now()
    values = dict(planes.iris())
    for p in query["params"]:
        name = p["name"]
        if name in values:
            continue
        if name == "key":
            h = (history or "").strip()
            prefix = h.partition(":")[0]
            explicit = re.match(r"^[a-z][a-z0-9+.-]*://", h) or h.startswith("urn:") or \
                (":" in h and prefix in definitions.load()["prefixes"])
            if explicit:
                values[name] = to_iri(h)
            else:
                parts = [x for x in h.strip("/").split("/")]
                if not parts or not all(_LOCAL.match(x) for x in parts):
                    raise BadInput(h)
                values[name] = f"{ns()}mem/{entity_name}/{'/'.join(parts)}"
        elif name == "now":
            values[name] = iso(t)
        elif name == "fresh_since":
            age = entity.get("lifecycle", {}).get("maxAge", "P2D")
            days = int(re.fullmatch(r"P(\d+)D", age).group(1)) if re.fullmatch(r"P(\d+)D", age) else 2
            values[name] = iso(t - _dt.timedelta(days=days))
        else:
            raw = filters.get(name)
            raw = None if raw is None else str(raw)
            field_def = next(f for f in entity["fields"] if f["name"] == name)
            if raw and _CONTROL.search(raw):
                raise BadInput(raw)
            if not raw:
                values[name] = ""
            elif field_def["kind"] == "iri":
                values[name] = (find_topic(client or quipu.Client(), raw) or to_iri(f"{TOPIC_NS}{slug(raw)}")) \
                    if name == "topic" else to_iri(raw)
            else:
                values[name] = raw
    client = client or quipu.Client()
    try:
        answer = client.ask(query["name"], values)
        rows = answer.get("rows", [])
    except quipu.QuipuError as exc:
        if isinstance(exc, quipu.Unreachable) or "unknown" not in str(exc).lower() and "not found" not in str(exc).lower():
            raise
        rows = client.query(render(query["template"], query["params"], values))
    rows = [{k: quipu.value(v) for k, v in r.items()} for r in rows]
    overflow = len(rows) > 50
    rows = rows[:50]
    lines = []
    for r in rows:
        date = r.get("recordedAt", "")[:10]
        text = r.get("body") or r.get("label", "")
        lines.append(f"[{r.get('kind', entity_name)}] {text} ({r.get('sourceKind', '')}, {date}) {r.get('iri', '')}")
    if not rows:
        lines.append(f"no current {entity_name} records match")
    if overflow:
        lines.append("more than 50 matches; narrow the filters")
    return Outcome(0, lines, {"query": query["name"], "rows": rows, "overflow": overflow})
