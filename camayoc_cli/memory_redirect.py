"""Memory-file redirect: a PreToolUse hook for writes under */.claude/projects/*/memory/*.

docs/design/agent-memory-entities.md §5-§6 (aegis-gshu.1). Claude memory files
pile up and go stale; this hook moves their content into typed camayoc entities.
It is OFF unless an installer (tambo) wires it into a harness, and it has two
modes once wired:

* ``copy`` (the default): the write goes ahead untouched. The hook spools the
  path, and a detached worker reads the file once the write lands, maps its
  frontmatter and body to one entity and records it through ``memory.record``
  (queued when quipu is unreachable). The agent never waits on it.
* ``block``: the write is refused (exit 2). The message names the exact
  ``cy record <entity> ...`` call that records the same thing.

``CAMAYOC_MEMORY_REDIRECT=off|copy|block`` overrides ``--mode``, so the hook can
be switched off without editing harness settings.

Every failure is logged and the hook exits 0; only block mode exits 2, and only
for a memory path. Nothing here prints or logs a memory file's text: the log
carries the path, the entity and cy's own outcome lines, which never echo a
value that tripped a secret rule. Values that trip one are redacted from the
suggested command too.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import definitions, memory
from .secret_rules import find_secrets

MODES = ("off", "copy", "block")
DEFAULT_MODE = "copy"
ENV_MODE = "CAMAYOC_MEMORY_REDIRECT"
ENV_PERSON = "CAMAYOC_PERSON"
ENV_VERIFY = "CAMAYOC_REDIRECT_VERIFY"  # a nonce: log it, map + precheck only, never contact a store
EDIT_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit", "apply_patch"}
MEMORY_PATH = re.compile(r"(?:^|/)\.claude/projects/[^/]+/memory/[^/]")
INDEX_FILE = "MEMORY.md"
MAX_STDIN = 4 * 1024 * 1024
LAND_TIMEOUT_S = 30.0
MAX_NEXT = 7
MAX_LINE = 240

# Deterministic cues (design §5 step 2). The frontmatter type alone never decides
# the kind (F1): it only picks the fallback when no cue is present.
DIRECTIVE_CUE = re.compile(r"\bDIRECTIVE\b|\bstanding (?:order|task|rule)\b|\b(?:never|always)\b", re.I)
DECISION_CUE = re.compile(r"\b(?:ruled|ruling|decided|decision|chose|chosen|go with)\b", re.I)
CONTEXT_CUES = (
    ("email", r"\be-?mails?\b|\bgmail\b|\binbox\b"),
    ("code-review", r"\bcode review\b|\bpull request\b|\bPRs?\b|\breview(?:s|er)?\b"),
    ("form", r"\bforms?\b|\bapplication\b"),
    ("travel", r"\btrips?\b|\btravel\b|\bflights?\b|\bhotel\b"),
    ("artifact", r"\bartifacts?\b"),
    ("document", r"\bdocs?\b|\bdocuments?\b|\bREADMEs?\b|\bwrite-?ups?\b"),
    ("chat", r"\bchat\b|\bDMs?\b|\bdiscord\b|\bslack\b|\blinkedin\b|\bmessages?\b"),
)
URL = re.compile(r"https?://[^\s)>\]\"'`]+")
BACKTICK = re.compile(r"`([^`\n]{3,200})`")
QUOTE = re.compile(r"[\"“]([^\"”\n]{8,400})[\"”]")
DATE = re.compile(r"\b(20\d\d-\d\d-\d\d)\b")
WHY = re.compile(r"\*\*Why:?\*\*:?\s*(.+?)(?:\n\s*\n|\n\*\*|\Z)", re.S)
BULLET = re.compile(r"^[-*]\s+(.+)$")


# ---- paths and state ----------------------------------------------------------

def state_dir(env=None) -> Path:
    env = os.environ if env is None else env
    root = Path(env.get("XDG_STATE_HOME") or Path(env.get("HOME") or Path.home()) / ".local/state")
    return root / "camayoc"


def log_path(env=None) -> Path:
    return state_dir(env) / "memory-redirect.jsonl"


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _scrub(line: str) -> str:
    return "<line withheld: it matched a secret rule>" if find_secrets(line) else line


def log(event: str, env=None, **fields) -> None:
    """Append one JSONL line; never raises (a hook must not fail on its own log)."""
    try:
        path = log_path(env)
        path.parent.mkdir(parents=True, exist_ok=True)
        if "lines" in fields:
            fields["lines"] = [_scrub(str(x)) for x in fields["lines"]][:20]
        row = {"ts": now_iso(), "event": event, **fields}
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    except Exception:  # noqa: BLE001
        pass


def is_memory_path(path: str) -> bool:
    return bool(path) and bool(MEMORY_PATH.search(str(path).replace("\\", "/")))


def _abs(path: str, cwd: str | None) -> str:
    p = Path(os.path.expanduser(path))
    if not p.is_absolute():
        p = Path(cwd or os.getcwd()) / p
    return os.path.normpath(str(p))


def resolve_mode(arg: str | None, env=None) -> str:
    env = os.environ if env is None else env
    for value in (env.get(ENV_MODE), arg):
        if value and value.strip().lower() in MODES:
            return value.strip().lower()
    return DEFAULT_MODE


# ---- the payload ----------------------------------------------------------------

def parse_patch(patch: str) -> list[tuple[str, str | None, str]]:
    """Codex native patch -> [(path, added-file content or None, op)].

    ``Add File`` carries its whole content (the ``+`` lines). An ``Update File``
    (with an optional ``Move to``) is read from disk once it lands. ``Delete
    File`` is reported so block mode can refuse it; copy mode skips it.
    """
    out: list[tuple[str, str | None, str]] = []
    current: list | None = None
    for line in patch.splitlines():
        m = re.match(r"^\*\*\* (Add|Update|Delete) File: (.+)$", line)
        if m:
            op, path = m.group(1).lower(), m.group(2).strip()
            current = [path, [] if op == "add" else None, op]
            out.append(current)  # type: ignore[arg-type]
            continue
        m = re.match(r"^\*\*\* Move to: (.+)$", line)
        if m and current is not None:
            current[0] = m.group(1).strip()
            continue
        if line.startswith("*** "):
            current = None if line.startswith("*** End") else current
            continue
        if current is not None and current[2] == "add" and line.startswith("+"):
            current[1].append(line[1:])
    return [(p, "\n".join(c) + "\n" if isinstance(c, list) else None, op) for p, c, op in out]


def targets(payload: dict) -> list[dict]:
    """The memory-path writes in one PreToolUse payload."""
    tool = payload.get("tool_name") or ""
    inp = payload.get("tool_input") or {}
    cwd = payload.get("cwd")
    found: list[dict] = []
    if tool not in EDIT_TOOLS or not isinstance(inp, dict):
        return found
    if tool == "apply_patch":
        patch = inp.get("command") or inp.get("patch") or inp.get("input") or ""
        if isinstance(patch, list):
            patch = "\n".join(str(x) for x in patch)
        for path, content, op in parse_patch(str(patch)):
            found.append({"path": _abs(path, cwd), "content": content, "op": op})
    else:
        path = inp.get("file_path") or inp.get("notebook_path") or inp.get("path") or ""
        content = inp.get("content") if tool == "Write" else None
        found.append({"path": _abs(str(path), cwd) if path else "", "content": content,
                      "op": "write" if tool == "Write" else "edit",
                      "preview": _preview_edit(_abs(str(path), cwd), inp) if path and tool != "Write" else None})
    return [t for t in found if is_memory_path(t["path"])]


def _preview_edit(path: str, inp: dict) -> str | None:
    """The file as an Edit/MultiEdit would leave it, for block mode's suggestion only.

    Copy mode never uses this: it reads what actually landed.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        text = ""
    edits = inp.get("edits") if isinstance(inp.get("edits"), list) else [inp]
    for e in edits:
        if not isinstance(e, dict):
            return None
        old, new = e.get("old_string"), e.get("new_string")
        if not isinstance(old, str) or not isinstance(new, str):
            return None
        if old == "":
            text = new + text
        elif old in text:
            text = text.replace(old, new) if e.get("replace_all") else text.replace(old, new, 1)
        else:
            return None
    return text


# ---- frontmatter + body -> one entity ----------------------------------------------

def _scalar(value: str):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    if value.startswith("[") and value.endswith("]"):
        return [_scalar(v) for v in value[1:-1].split(",") if v.strip()]
    return value


def _parse_block(rows: list[tuple[int, str]], i: int, indent: int):
    """rows[i:] at ``indent`` -> (dict or list, next index)."""
    if i < len(rows) and rows[i][1].startswith("- "):
        items = []
        while i < len(rows) and rows[i][0] == indent and rows[i][1].startswith("- "):
            items.append(_scalar(rows[i][1][2:]))
            i += 1
        return items, i
    node: dict = {}
    while i < len(rows) and rows[i][0] == indent:
        key, sep, value = rows[i][1].partition(":")
        i += 1
        if not sep:
            continue
        if value.strip():
            node[key.strip()] = _scalar(value)
        elif i < len(rows) and (rows[i][0] > indent or (rows[i][0] == indent and rows[i][1].startswith("- "))):
            node[key.strip()], i = _parse_block(rows, i, rows[i][0])
        else:
            node[key.strip()] = ""
    return node, i


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """A small YAML subset: nested mappings, ``- item`` lists, quoted scalars.

    Memory files carry only that much (name, description, metadata.type), and
    the explicit ``camayoc:`` block needs nothing more. A line deeper than its
    block expects is skipped rather than guessed at.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() in ("---", "..."))
    except StopIteration:
        return {}, text
    rows = [(len(r) - len(r.lstrip(" ")), r.strip()) for r in lines[1:end]
            if r.strip() and not r.lstrip().startswith("#")]
    root: dict = {}
    i = 0
    while i < len(rows):
        node, j = _parse_block(rows, i, rows[i][0])
        if isinstance(node, dict):
            root.update(node)
        i = j if j > i else i + 1
    return root, "\n".join(lines[end + 1:]).strip("\n")


def _first_sentence(text: str, limit: int = MAX_LINE) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    text = re.sub(r"^(?:directive|rule|preference|decision|reference)\s*[-:—]+\s*", "", text, flags=re.I)
    m = re.match(r"(.+?[.!?])(?:\s|$)", text)
    out = (m.group(1) if m else text).strip()
    out = out[:1].upper() + out[1:]
    return out if len(out) <= limit else out[: limit - 3].rstrip() + "..."


def _topic(fm: dict, filename: str) -> str:
    name = str(fm.get("name") or Path(filename).stem)
    return memory.slug(re.sub(r"^handoff-?", "", name)) or memory.slug(Path(filename).stem)


def _bullets(body: str, limit: int) -> list[str]:
    out = []
    for line in body.splitlines():
        m = BULLET.match(line)  # top-level bullets only
        if m:
            text = re.sub(r"\s+", " ", m.group(1)).strip()
            out.append(text if len(text) <= MAX_LINE else text[: MAX_LINE - 3] + "...")
        if len(out) >= limit:
            break
    return out


def _explicit(fm: dict) -> dict | None:
    meta = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
    block = fm.get("camayoc") if isinstance(fm.get("camayoc"), dict) else meta.get("camayoc")
    return block if isinstance(block, dict) else None


def classify(fm: dict, body: str, filename: str) -> str:
    entities = definitions.load()["entities"]
    explicit = _explicit(fm)
    if explicit and str(explicit.get("entity", "")) in entities:
        return str(explicit["entity"])
    name = str(fm.get("name") or Path(filename).stem)
    if name.lower().startswith("handoff") or Path(filename).name.lower().startswith("handoff"):
        return "handoff"
    meta = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
    kind = str(fm.get("type") or meta.get("type") or "").lower()
    text = f"{fm.get('description', '')}\n{body}"
    if kind == "user":
        return "preference"
    if kind == "reference":
        return "reference"
    if DIRECTIVE_CUE.search(text):
        return "directive"
    if DECISION_CUE.search(text):
        return "decision"
    if kind == "project" and (URL.search(body) or BACKTICK.search(body)):
        return "reference"
    return "directive" if kind == "feedback" else "decision"


def _context(*texts: str) -> str | None:
    """The closed Preference context whose cue appears first (description, then body)."""
    for text in texts:
        hits = [(m.start(), name) for name, rx in CONTEXT_CUES for m in [re.search(rx, text or "", re.I)] if m]
        if hits:
            return min(hits)[1]
    return None


def plan(text: str, filename: str, *, person: str | None = None, env=None) -> dict:
    """Map one memory file to ``{"entity", "values", "flags"}`` for memory.record.

    Explicit fields in a ``camayoc:`` frontmatter block (top level or under
    ``metadata``) win; the rest are derived deterministically. A field this
    cannot derive is left out, so cy refuses with the shape's plain-word
    message and the log says exactly what is missing.
    """
    env = os.environ if env is None else env
    fm, body = parse_frontmatter(text)
    entity_name = classify(fm, body, filename)
    entity = definitions.load()["entities"][entity_name]
    field_names = {f["name"] for f in entity["fields"]}
    person = person or env.get(ENV_PERSON) or None
    agent = memory.agent_name(env)
    desc = str(fm.get("description") or "")
    topic = _topic(fm, filename)
    quote = QUOTE.search(body)
    why = WHY.search(body)
    date = DATE.search(body) or DATE.search(str(fm.get("name") or filename))
    url = URL.search(body)
    tick = BACKTICK.search(body)
    summary = _first_sentence(desc) if desc else _first_sentence(body)

    derived: dict[str, object] = {}
    if entity_name == "directive":
        derived = {"rule": summary, "issuedBy": person, "appliesTo": agent if agent and ":" not in agent else "crew",
                   "topic": topic, "issuedOn": date.group(1) if date else None,
                   "why": _first_sentence(why.group(1), 400) if why else None,
                   "verbatim": quote.group(1).strip() if quote else None}
    elif entity_name == "preference":
        derived = {"person": person, "prefers": summary, "context": _context(desc, body), "topic": topic,
                   "verbatim": quote.group(1).strip() if quote else None}
    elif entity_name == "decision":
        derived = {"chose": summary, "by": person, "about": agent if agent and ":" not in agent else "crew",
                   "topic": topic, "rationale": _first_sentence(why.group(1), 400) if why else None,
                   "verbatim": quote.group(1).strip() if quote else None}
    elif entity_name == "reference":
        locator = url.group(0) if url else (tick.group(1).strip() if tick else None)
        derived = {"subject": None, "topic": topic, "locator": locator}
    elif entity_name == "handoff":
        derived = {"agent": agent if agent and ":" not in agent else None, "next": _bullets(body, MAX_NEXT)}

    explicit = _explicit(fm) or {}
    values = {k: v for k, v in derived.items() if k in field_names and v not in (None, "", [])}
    for k, v in explicit.items():
        if k in field_names and v not in (None, "", [], {}):
            values[k] = v
    flags = {k: bool(explicit.get(k)) for k in (entity["sourceKind"].get("flags") or {}) if explicit.get(k)}
    return {"entity": entity_name, "values": values, "flags": flags}


# ---- block mode ---------------------------------------------------------------------

def command_for(p: dict) -> str:
    """The exact ``cy record`` call for a plan, with placeholders and redactions."""
    entity = definitions.load()["entities"][p["entity"]]
    parts = ["cy", "record", entity["noun"]]
    for f in entity["fields"]:
        vals = memory._values(p["values"].get(f["name"]))
        if not vals and f["required"]:
            parts += [f["flag"], f"<{f['name']}>"]
        for v in vals:
            rules = find_secrets(v) if f["name"] != "secretRef" else []
            parts += [f["flag"], f"<redacted: looks like a secret ({', '.join(rules)}); use --secret-ref>"
                      if rules else shlex.quote(v)]
    parts += [f"--{k}" for k, on in (p.get("flags") or {}).items() if on]
    return " ".join(parts)


def block_message(target: dict, p: dict | None) -> str:
    path = target["path"]
    if Path(path).name == INDEX_FILE:
        return ("Not written: memory files are retired here; the index is replaced by recall. "
                "Run `cy recall <entity>` (or `cy prime`) instead, and record new facts with `cy record`.")
    if p is None:
        return ("Not written: memory files are retired here. Record this with `cy record <entity> ...` "
                "(`cy record --help` lists the entities).")
    return ("Not written: memory files are retired here; record it in Quipu instead:\n  "
            + command_for(p)
            + "\nFill any <placeholder>; `cy record " + p["entity"] + " --help` explains each field. "
            "An unregistered topic is refused; register it with `cy topic add <name>`.")


# ---- copy mode: spool + detached worker ---------------------------------------------

def _signature(path: str) -> dict:
    try:
        st = os.stat(path)
        with open(path, "rb") as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()
        return {"exists": True, "mtime_ns": st.st_mtime_ns, "size": st.st_size, "sha": digest}
    except OSError:
        return {"exists": False}


def spool(target: dict, *, person: str | None, env=None) -> Path:
    env = os.environ if env is None else env
    d = state_dir(env) / "redirect-spool"
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    job = {"path": target["path"], "content": target.get("content"), "op": target.get("op"),
           "baseline": _signature(target["path"]) if target.get("content") is None else None,
           "expect": hashlib.sha256(target["content"].encode("utf-8")).hexdigest()
           if target.get("content") is not None else None,
           "person": person, "verify": env.get(ENV_VERIFY)}
    fd, name = tempfile.mkstemp(prefix="job-", suffix=".json", dir=d)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(job, fh)
    return Path(name)


def spawn_worker(job: Path) -> None:
    root = str(Path(__file__).resolve().parents[1])
    env = dict(os.environ)
    env["PYTHONPATH"] = root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    subprocess.Popen([sys.executable, "-m", "camayoc_cli", "hook", "memory-redirect", "--worker", str(job)],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True, close_fds=True, env=env, cwd=root)


def wait_for_landing(path: str, baseline: dict | None, timeout: float = LAND_TIMEOUT_S,
                     expect: str | None = None) -> bool:
    """True once the file differs from ``baseline`` (an edit) or holds ``expect`` (a write).

    Copy mode records only what actually landed: a write the harness denied
    is never recorded.
    """
    deadline = time.monotonic() + timeout
    while True:
        sig = _signature(path)
        if (sig.get("sha") == expect) if expect else (baseline is None or sig != baseline):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.2)


def run_job(job_path: Path, env=None, timeout: float = LAND_TIMEOUT_S) -> dict:
    """Worker body: read the spooled job, map the landed file, record it. Never raises."""
    env = os.environ if env is None else env
    try:
        job = json.loads(job_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        log("error", env, stage="spool", error=type(exc).__name__)
        return {"event": "error"}
    finally:
        try:
            job_path.unlink()
        except OSError:
            pass
    path, verify = job["path"], job.get("verify")
    base = {"path": path, **({"verify": verify} if verify else {})}
    try:
        if Path(path).name == INDEX_FILE:
            log("skipped", env, reason="index file; recall replaces it", **base)
            return {"event": "skipped"}
        if job.get("op") == "delete":
            log("skipped", env, reason="delete", **base)
            return {"event": "skipped"}
        text = job.get("content")
        if not verify:  # a verify probe maps the payload; nothing is ever written for it
            if not wait_for_landing(path, job.get("baseline"), timeout, job.get("expect")):
                log("skipped", env, reason="the write never landed", **base)
                return {"event": "skipped"}
            if text is None:
                text = Path(path).read_text(encoding="utf-8", errors="replace")
        if text is None:
            log("skipped", env, reason="nothing to map", **base)
            return {"event": "skipped"}
        p = plan(text, path, person=job.get("person"), env=env)
        if verify:
            # A verify probe proves the worker ran and the mapping works; it never
            # contacts a store, so it can run against any host safely.
            problems = memory.precheck(definitions.load()["entities"][p["entity"]], p["values"])
            log("verified", env, entity=p["entity"], code=2 if problems else 0, lines=problems, **base)
            return {"event": "verified", "entity": p["entity"], "code": 2 if problems else 0, "lines": problems}
        out = memory.record_or_queue(p["entity"], p["values"], flags=p["flags"], env=env)
        status = out.data.get("status") or ("recorded" if out.code == 0 else "refused")
        event = "refused" if out.code else ("queued" if status == "queued" else "recorded")
        log(event, env, entity=p["entity"], code=out.code, lines=out.lines, **base)
        return {"event": event, "entity": p["entity"], "code": out.code, "lines": out.lines}
    except memory.BadInput:
        log("refused", env, reason="a derived value cannot be an IRI or query value", **base)
        return {"event": "refused"}
    except Exception as exc:  # noqa: BLE001 - logged, never raised into the harness
        log("error", env, stage="record", error=type(exc).__name__, **base)
        return {"event": "error"}


# ---- entry point ---------------------------------------------------------------------

def main(mode_arg: str | None = None, person: str | None = None, worker: str | None = None,
         stdin=None, env=None) -> int:
    env = os.environ if env is None else env
    if worker:
        run_job(Path(worker), env)
        return 0
    try:
        raw = (stdin or sys.stdin).read(MAX_STDIN)
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            return 0
        found = targets(payload)
        if not found:
            return 0
        mode = resolve_mode(mode_arg, env)
        if mode == "off":
            return 0
        person = person or env.get(ENV_PERSON)
        verify = env.get(ENV_VERIFY)
        for t in found:
            log("fired", env, mode=mode, tool=payload.get("tool_name"), path=t["path"],
                **({"verify": verify} if verify else {}))
        if mode == "block":
            msgs = []
            for t in found:
                p = None
                text = t["content"] if t.get("content") is not None else t.get("preview")
                if text is not None and Path(t["path"]).name != INDEX_FILE:
                    try:
                        p = plan(text, t["path"], person=person, env=env)
                    except Exception:  # noqa: BLE001
                        p = None
                msgs.append(block_message(t, p))
            print("\n".join(dict.fromkeys(msgs)), file=sys.stderr)
            return 2
        for t in found:
            try:
                spawn_worker(spool(t, person=person, env=env))
            except Exception as exc:  # noqa: BLE001
                log("error", env, stage="spawn", error=type(exc).__name__, path=t["path"])
        return 0
    except Exception as exc:  # noqa: BLE001 - a hook never breaks the agent
        log("error", env, stage="hook", error=type(exc).__name__)
        return 0
