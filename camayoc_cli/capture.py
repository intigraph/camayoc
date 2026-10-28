"""Memory capture by code: prompts, final answers and explicit notes as untyped
Quipu episodes (docs/design/memory-capture.md, agent-memory-entities.md §5).

A capture is a `prov:Activity` in the `crew:inferred` plane with the verbatim
text on `rdfs:comment`. It carries no camayoc entity type, so no entity shape
applies and the write is never refused for content. Only promotion
(promote.py) has to pass the shapes.

Hooks must never slow or break a session, so a hook only appends to a local
spool file and starts a detached `cy capture flush`; the network write happens
there. Every hook path exits 0.
"""
from __future__ import annotations

import datetime as _dt
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

from . import memory, planes, quipu
from .secret_rules import RULES, find_secrets

PROV = "http://www.w3.org/ns/prov#"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
XSD = "http://www.w3.org/2001/XMLSchema#"
GROUP = "camayoc-capture"
SOURCES = ("prompt", "answer", "explicit", "stop-check", "file-redirect", "import")
MAX_TEXT = 4000
MIN_PROMPT = 20
BATCH = 25

# Prompts the harness (not a person) wrote: notifications, slash-command
# expansions, hook feedback. They are not knowledge.
_HARNESS_PROMPT = re.compile(r"^\s*<(?:task-notification|command-name|command-message|local-command-stdout|"
                             r"system-reminder|user-prompt-submit-hook)\b")

# ---- deterministic kind hints (promotion candidates) --------------------------
# A capture with no hint is evidence only and is never a promotion candidate,
# so ordinary prompts and answers cost one write and nothing more.
CY_LINE = re.compile(r"(?m)(?:^|(?<=[\s`]))(?:\$\s*)?(?:cy|camayoc)\s+record\s+([a-z]+)\b")
MARKER = re.compile(r"(?m)^\s*(DIRECTIVE|DECIDED|DECISION|RULING)\b")
_ISSUER_PREFIX = r"(?m)^\s*({names})\s*(?:\([^)\n]{{0,40}}\))?\s*:"
_ALWAYS = re.compile(r"(?i)\b(?:never|always|must not|do not|don't)\b")


# A prompt that is a relayed crew or harness message, not the person's own
# words: shantytown's `[from <agent>] ` signature (attribution.py), scheduled
# job and fleet envelopes, agent-message and tool-output blocks. Such a prompt
# is captured as evidence but is never a promotion candidate, whatever
# DECIDED:/Stiwi: lines it quotes.
RELAY = re.compile(r"^\s*(?:\[(?:from|job|st|shanty|relay|fwd)\b[^\]\n]*\]|"
                   r"<(?:agent-message|agent_message|inbox|message|tool_result|tool-result|function_results|"
                   r"tool_output|subagent|teammate-message)\b|(?:from|relayed from|forwarded from)\s+\S+\s*:)",
                   re.I)


def is_relayed(text: str) -> bool:
    return bool(RELAY.match(text or ""))


def first_line(text: str) -> str:
    return next((ln for ln in (text or "").splitlines() if ln.strip()), "")


def kind_hint(text: str, issuers: dict | None = None, source: str = "explicit") -> str | None:
    """`cy:<entity>`, `directive`, `decision`, or None. Regex only, no model.

    Only words a person wrote promote. An `answer` is the agent talking (it may
    quote a ruling, which does not make it one): never a candidate. A `prompt`
    is a candidate only when it is not a relayed message and the marker or
    issuer prefix opens it; a marker further down is quoted text (a pasted
    handoff, a memory line). Explicit notes and producers (redirect, import,
    stop-check) are scanned whole: an agent or producer chose to file them.
    """
    if source == "answer":
        return None
    if source == "prompt":
        if is_relayed(text):
            return None
        m = CY_LINE.search(text)
        if m:
            return f"cy:{m.group(1)}"
        text = first_line(text)
    else:
        m = CY_LINE.search(text)
        if m:
            return f"cy:{m.group(1)}"
    m = MARKER.search(text)
    if m:
        return "directive" if m.group(1) == "DIRECTIVE" else "decision"
    names = sorted(issuers or {}, key=len, reverse=True)
    if names and re.search(_ISSUER_PREFIX.format(names="|".join(re.escape(n) for n in names)), text):
        return "directive" if _ALWAYS.search(text) else "decision"
    return None


# ---- configuration ---------------------------------------------------------------

def config_path(env=None) -> Path:
    env = os.environ if env is None else env
    root = Path(env.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return Path(env.get("CAMAYOC_CAPTURE_CONFIG") or root / "camayoc" / "capture.json")


def load_config(env=None) -> dict:
    """{"issuers": {"Stiwi": "person-stiwi"}, "prompt_author": "person-stiwi",
    "extractor": ["argv", ...]}. Missing or unreadable means no issuers."""
    try:
        doc = json.loads(config_path(env).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def enabled(env=None) -> bool:
    """Capture is opt-in: tambo sets CAMAYOC_CAPTURE=1 (gshu.9)."""
    env = os.environ if env is None else env
    return env.get("CAMAYOC_CAPTURE", "") in ("1", "true", "yes")


# ---- text hygiene ------------------------------------------------------------------

def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def content_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(normalize(text).encode()).hexdigest()


def redact(text: str) -> str | None:
    """Text with named secret matches replaced; None when a secret remains
    (an unlabeled high-entropy string cannot be cut out reliably)."""
    for name, rx in RULES:
        text = rx.sub(f"[redacted:{name}]", text)
    return None if find_secrets(text) else text


def clip(text: str) -> str:
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT - 20] + " [clipped]"


# ---- the record ----------------------------------------------------------------------

def build(text: str, source: str, *, session: str | None = None, agent: str | None = None,
          kind: str | None = None, about: str | None = None, at: _dt.datetime | None = None,
          env=None) -> dict | None:
    """One spool entry, or None when there is nothing to keep."""
    if source not in SOURCES:
        raise ValueError(f"captureSource must be one of {', '.join(SOURCES)}")
    text = redact(text or "")
    if text is None or not normalize(text):
        return None
    text = clip(text.strip())
    t = at or _now(env)
    h = content_hash(text)
    entry = {"text": text, "source": source, "hash": h, "at": memory.iso(t),
             "agent": agent or memory.agent_name(env), "session": session}
    hint = kind or kind_hint(text, load_config(env).get("issuers"), source)
    if hint:
        entry["kind"] = hint
    if about:
        entry["about"] = about
    return entry


def _now(env=None) -> _dt.datetime:
    fixed = (os.environ if env is None else env).get("CAMAYOC_NOW")
    if fixed:
        return _dt.datetime.fromisoformat(fixed.replace("Z", "+00:00")).astimezone(_dt.timezone.utc)
    return memory.now()


def client_for(env=None, timeout: float = 15.0) -> quipu.Client:
    """A client for the store `env` names (QUIPU_SERVER), not just the process's."""
    return quipu.Client((os.environ if env is None else env).get("QUIPU_SERVER") or None, timeout=timeout)


def capture_iri(entry: dict) -> str:
    stamp = entry["at"].replace("-", "").replace(":", "")
    return f"{memory.ns()}mem/capture/{stamp}-{entry['hash'][7:19]}"


def turtle(entry: dict) -> str:
    a = memory.ns()
    first = normalize(entry["text"])[:80]
    # No rdf:type on purpose: a strict store refuses any class no loaded shape
    # sanctions, and a capture must never be refused for its kind. Captures
    # are found by aegis:captureSource.
    triples = [f"<{RDFS}label> {memory.lit('capture ' + entry['source'] + ': ' + first)}",
               f"<{RDFS}comment> {memory.lit(entry['text'])}",
               f"<{a}groupId> {memory.lit(GROUP)}",
               f"<{a}captureSource> {memory.lit(entry['source'])}",
               f'<{a}capturedAt> "{entry["at"]}"^^<{XSD}dateTime>',
               f"<{a}recordedBy> {memory.lit(entry['agent'])}",
               f"<{a}contentHash> {memory.lit(entry['hash'])}"]
    session = entry.get("session")
    if session and re.fullmatch(r"[A-Za-z0-9._:-]{1,160}", session):
        iri = session if session.startswith("urn:") else f"urn:camayoc:session:claude:{session}"
        triples.append(f"<{PROV}wasAssociatedWith> {memory.iri_ref(iri)}")
    if entry.get("kind") and re.fullmatch(r"[a-z]+(?::[a-z]+)?", entry["kind"]):
        triples.append(f"<{a}kindHint> {memory.lit(entry['kind'])}")
    if entry.get("about"):
        triples.append(f"<{a}about> {memory.iri_ref(memory.to_iri(entry['about']))}")
    return f"{memory.iri_ref(capture_iri(entry))}\n    " + " ;\n    ".join(triples) + " .\n"


# ---- spool (the hook side) -------------------------------------------------------------
# The hook appends one line with O_APPEND and takes no lock, so it can never
# wait on a flush. `cy capture flush` holds its own lock (flush.lock) only to
# keep two flushers apart: it renames the spool to an in-flight file and
# writes from that, so new hook lines land in a fresh spool meanwhile.
# Retention: entries older than MAX_AGE_DAYS are dropped, then the oldest
# beyond MAX_ENTRIES / MAX_BYTES, so a long outage cannot pile captures up;
# the hook itself drops a capture once the spool passes HARD_BYTES.

MAX_AGE_DAYS = 7
MAX_ENTRIES = 5000
MAX_BYTES = 4 * 1024 * 1024
HARD_BYTES = 2 * MAX_BYTES


def state_dir(env=None) -> Path:
    env = os.environ if env is None else env
    return Path(env.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "camayoc"


def spool_path(env=None) -> Path:
    return state_dir(env) / "capture-spool.jsonl"


def inflight_path(env=None) -> Path:
    return state_dir(env) / "capture-spool.inflight.jsonl"


def _private_dir(env=None) -> Path:
    d = state_dir(env)
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(d, 0o700)  # an existing dir keeps its old mode otherwise
    except OSError:
        pass
    return d


def _open_private(path: Path, flags: int) -> int:
    fd = os.open(path, flags | os.O_CREAT, 0o600)
    try:
        os.fchmod(fd, 0o600)
    except OSError:
        pass
    return fd


def _line(entry: dict) -> bytes:
    return (json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def spool(entry: dict, env=None) -> bool:
    """Append one capture. No lock: one O_APPEND write. False when dropped
    because the spool is past its hard ceiling."""
    _private_dir(env)
    path = spool_path(env)
    size = 0
    for p in (path, inflight_path(env)):
        try:
            size += p.stat().st_size
        except OSError:
            pass
    if size > HARD_BYTES:
        return False
    fd = _open_private(path, os.O_WRONLY | os.O_APPEND)
    try:
        os.write(fd, _line(entry))
    finally:
        os.close(fd)
    return True


def _read(path: Path) -> list[dict]:
    try:
        lines = path.read_bytes().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue  # a torn line from a crash is dropped, never retried forever
    return out


def spooled(env=None) -> list[dict]:
    return _read(inflight_path(env)) + _read(spool_path(env))


def retain(entries: list[dict], now: _dt.datetime) -> tuple[list[dict], int]:
    """Entries kept by the retention bound (age, then count and bytes, oldest
    dropped first), and how many were dropped."""
    cutoff = memory.iso(now - _dt.timedelta(days=MAX_AGE_DAYS))
    kept = [e for e in entries if str(e.get("at", "")) >= cutoff]
    kept = kept[-MAX_ENTRIES:]
    size, start = 0, len(kept)
    for i in range(len(kept) - 1, -1, -1):
        size += len(_line(kept[i]))
        if size > MAX_BYTES:
            break
        start = i
    kept = kept[start:]
    return kept, len(entries) - len(kept)


def _write_private(path: Path, entries: list[dict]) -> None:
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    fd = _open_private(tmp, os.O_WRONLY | os.O_TRUNC)
    try:
        os.write(fd, b"".join(_line(e) for e in entries))
    finally:
        os.close(fd)
    os.replace(tmp, path)


@contextmanager
def _flush_lock(env=None, wait: bool = False):
    """Yields True when this process holds the flush lock, False when another
    flush has it (never blocks unless wait=True)."""
    fd = _open_private(_private_dir(env) / "flush.lock", os.O_WRONLY)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX if wait else fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def flush_running(env=None) -> bool:
    with _flush_lock(env) as held:
        return not held


def flush(client: quipu.Client | None = None, env=None) -> memory.Outcome:
    """Write every spooled capture, BATCH per /knot, oldest first. Stops at the
    first unreachable store and keeps the rest in the in-flight file."""
    with _flush_lock(env) as held:
        if not held:
            n = len(spooled(env))
            return memory.Outcome(1, ["another flush is running"], {"written": 0, "spooled": n})
        return _flush(client or client_for(env, timeout=10.0), env)


def _flush(client: quipu.Client, env=None) -> memory.Outcome:
    graph = planes.plane_for("inferred")
    spool_file, inflight = spool_path(env), inflight_path(env)
    if spool_file.exists():
        if inflight.exists():  # a stopped flush left work: keep order, old first
            _write_private(inflight, _read(inflight) + _read(spool_file))
            spool_file.unlink()
        else:
            os.replace(spool_file, inflight)
    entries, dropped = retain(_read(inflight), _now(env))
    lines = [f"retention dropped {dropped}"] if dropped else []
    done = 0
    while done < len(entries):
        batch = entries[done:done + BATCH]
        try:
            client.knot("".join(turtle(e) for e in batch), graph, batch[0]["agent"], "camayoc:cy capture")
        except quipu.Unreachable as exc:
            lines.append(f"stopped: {exc}")
            break
        except quipu.QuipuError as exc:
            # A refused batch is retried one entry at a time so one bad entry
            # cannot hold the spool; a refused single entry is dropped with a
            # line naming its hash (never its text).
            lines.append(f"batch retried singly after: {str(exc)[:160]}")
            stopped = False
            for e in batch:
                try:
                    client.knot(turtle(e), graph, e["agent"], "camayoc:cy capture")
                except quipu.Unreachable as down:
                    lines.append(f"stopped: {down}")
                    stopped = True
                    break
                except quipu.QuipuError as one:
                    lines.append(f"dropped {e['hash'][:19]}: {str(one)[:160]}")
                done += 1
            if stopped:
                break
            continue
        done += len(batch)
    rest = entries[done:]
    if rest:
        _write_private(inflight, rest)
    else:
        inflight.unlink(missing_ok=True)
    lines.append(f"captured {done}, {len(rest) + len(_read(spool_file))} still spooled")
    return memory.Outcome(0 if not rest else 1, lines, {"written": done, "spooled": len(rest)})


def flush_detached(env=None) -> None:
    """Start `cy capture flush` with no terminal and no wait."""
    if (env or os.environ).get("CAMAYOC_CAPTURE_SYNC") == "1":  # tests
        flush(env=env)
        return
    if flush_running(env):
        return  # the running flush, or the next hook, writes this entry
    main = Path(__file__).resolve().parent / "__main__.py"
    try:
        subprocess.Popen([sys.executable, str(main), "capture", "flush"], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
                         env=dict(env) if env is not None else None)
    except OSError:
        pass  # the spool keeps it; the next hook or `cy capture flush` writes it


# ---- harness hooks ------------------------------------------------------------------------

def last_assistant_text(transcript: str, budget: int = 400_000) -> str | None:
    """The text of the final assistant message in a Claude Code transcript (JSONL)."""
    try:
        with open(transcript, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - budget))
            tail = f.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in reversed(tail):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("type") != "assistant":
            continue
        content = (rec.get("message") or {}).get("content")
        if isinstance(content, str) and content.strip():
            return content
        if isinstance(content, list):
            text = "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
            if text.strip():
                return text
    return None


def hook(event: str, payload: dict, env=None) -> memory.Outcome:
    """UserPromptSubmit -> prompt capture; Stop -> answer capture; SessionEnd ->
    promotion of this session's candidates. Never raises, never blocks."""
    env = os.environ if env is None else env
    if not enabled(env):
        return memory.Outcome(0, ["capture disabled (set CAMAYOC_CAPTURE=1)"])
    session = payload.get("session_id")
    session = session if isinstance(session, str) else None
    try:
        if event == "user-prompt-submit":
            text = payload.get("prompt") or ""
            if len(text.strip()) < MIN_PROMPT or _HARNESS_PROMPT.match(text):
                return memory.Outcome(0, ["skipped"])
            entry = build(text, "prompt", session=session, env=env)
        elif event == "stop":
            text = payload.get("last_assistant_message")
            if not isinstance(text, str) or not text.strip():
                path = payload.get("transcript_path")
                text = last_assistant_text(path) if isinstance(path, str) else None
            if not text:
                return memory.Outcome(0, ["no final answer found"])
            entry = build(text, "answer", session=session, env=env)
        elif event == "session-end":
            flush_detached(env)
            _promote_detached(session, env)
            return memory.Outcome(0, ["promotion started"])
        else:
            return memory.Outcome(0, [f"unknown hook event {event}"])
        if entry is None:
            return memory.Outcome(0, ["nothing kept"])
        if not spool(entry, env):
            return memory.Outcome(0, ["spool full; capture dropped"])
        flush_detached(env)
        return memory.Outcome(0, ["spooled"])
    except Exception as exc:  # noqa: BLE001 - a hook never breaks a session
        return memory.Outcome(0, [f"capture skipped: {type(exc).__name__}"])


def _promote_detached(session: str | None, env=None) -> None:
    if (env or os.environ).get("CAMAYOC_CAPTURE_SYNC") == "1":
        from . import promote
        promote.run(session=session, env=env)
        return
    main = Path(__file__).resolve().parent / "__main__.py"
    argv = [sys.executable, str(main), "promote"]
    if session and re.fullmatch(r"[A-Za-z0-9._-]{1,128}", session):
        argv += ["--session", session]
    try:
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True, env=dict(env) if env is not None else None)
    except OSError:
        pass


def note(text: str, *, kind: str | None = None, about: str | None = None, env=None,
         client: quipu.Client | None = None, source: str = "explicit") -> memory.Outcome:
    """`cy note`: an explicit capture, written now (spooled if quipu is down)."""
    if about:
        memory.to_iri(about)  # BadInput before anything is kept
    entry = build(text, source, session=_session_id(env), kind=kind, about=about, env=env)
    if entry is None:
        return memory.Outcome(2, ["Not captured: the note is empty or holds a secret that cannot be redacted."])
    if not spool(entry, env):
        return memory.Outcome(3, ["Not captured: the capture spool is full; run `cy capture flush` once quipu is back."])
    out = flush(client=client, env=env)
    iri = capture_iri(entry)
    dropped = [ln for ln in out.lines if ln.startswith(("dropped", "batch retried"))]
    if dropped:
        return memory.Outcome(3, [f"Not captured: quipu refused the write ({' '.join(dropped)[:300]})."])
    if out.data.get("spooled"):
        return memory.Outcome(0, [f"queued {iri} ({out.lines[0]}); run `cy capture flush` once quipu is back"],
                              {"iri": iri, "status": "queued"})
    hint = f" (promotion candidate: {entry['kind']})" if entry.get("kind") else ""
    return memory.Outcome(0, [f"captured {iri}{hint}"], {"iri": iri, "status": "captured", "kind": entry.get("kind")})


def _session_id(env=None) -> str | None:
    env = os.environ if env is None else env
    for key in ("CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID", "CODEX_THREAD_ID"):
        if env.get(key):
            return env[key]
    return None
