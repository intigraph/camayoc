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
import hashlib
import json
import os
import re
import subprocess
import sys
from contextlib import contextmanager, ExitStack
from pathlib import Path

from . import capture_spool, memory, offline, planes, quipu
from .secret_rules import RULES, find_secrets

PROV = "http://www.w3.org/ns/prov#"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
XSD = "http://www.w3.org/2001/XMLSchema#"
GROUP = "camayoc-capture"
SOURCES = ("prompt", "answer", "explicit", "stop-check", "file-redirect", "import")
MAX_TEXT = 4000
MIN_PROMPT = 20
BATCH = 25
MAX_AGE_DAYS = 7

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
    is a candidate only when it is not a relayed message and the marker,
    issuer prefix or `cy record` call opens it; a marker further down is quoted text (a pasted
    handoff, a memory line). Explicit notes and producers (redirect, import,
    stop-check) are scanned whole: an agent or producer chose to file them.
    """
    if source == "answer":
        return None
    if source == "prompt":
        if is_relayed(text):
            return None
        text = first_line(text)
        m = CY_LINE.search(text)
        if m:
            return f"cy:{m.group(1)}"
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
# New writers use the shared durable FIFO. Legacy in-flight work is a separate
# older prefix; it is never discarded or merged by a destructive rename.
OFFLINE_FIFO_V1 = True


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
    os.chmod(d, 0o700)
    return d


def _line(entry: dict) -> bytes:
    return (json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def spool(entry: dict, env=None) -> bool:
    """Durable bounded-cost append; replay handles expiry and quarantine.

    Preserve the producer's boolean contract. Hooks always exit zero.
    """
    _private_dir(env)
    context = memory._queued_context(env)
    origin = dict(context['env'], QUIPU_AGENT=entry['agent'])
    if entry.get('session'):
        origin['QUIPU_SESSION'] = entry['session']
    context['env'] = origin
    path = spool_path(env)
    try:
        capture_spool.append(path, inflight_path(env), dict(entry, _offline_context=context))
    except offline.QueueError:
        return False
    return True


def spooled(env=None) -> list[dict]:
    # Malformed, symlinked, over-capacity or incomplete files remain UNKNOWN.
    return offline._read(inflight_path(env)) + offline._read(spool_path(env))


@contextmanager
def _flush_lock(env=None):
    _private_dir(env)
    with ExitStack() as stack:
        try:
            stack.enter_context(offline.locked(spool_path(env), replay=True))
        except BlockingIOError:
            yield False
            return
        yield True


def flush_running(env=None) -> bool:
    with _flush_lock(env) as held:
        return not held


def _replay_entry(raw, env):
    """The same local eligibility checks for normal replay and explicit requeue."""
    entry = None
    try:
        entry = json.loads(raw)
        if not raw.endswith(b'\n') or len(raw) > offline.MAX_RECORD_BYTES or not isinstance(entry, dict):
            raise ValueError('invalid capture fragment')
        stamp = _dt.datetime.fromisoformat(entry['at'].replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            raise ValueError('capture needs an aware timestamp')
        if stamp < _now(env) - _dt.timedelta(days=MAX_AGE_DAYS):
            return entry, 'expired'
        context = entry.get('_offline_context')
        current = memory._queued_context(env)
        if (not isinstance(context, dict) or not context.get('server') or
                not isinstance(context.get('env'), dict) or
                context.get('server') != current['server'] or
                context.get('plane_ns') != current['plane_ns']):
            return entry, 'destination'
        turtle(entry)
        return entry, None
    except (ValueError, KeyError, TypeError, AttributeError):
        return entry, 'malformed'


def quarantine(env=None, *, clear=False, requeue=False) -> memory.Outcome:
    """Operator-only local inspection/discard, with no network or raw text output."""
    try:
        path = spool_path(env)
        if clear and requeue:
            raise ValueError('choose clear or requeue')
        if requeue:
            def accept(raw):
                entry, reason = _replay_entry(raw, env)
                return entry, reason
            with _flush_lock(env) as held:
                if not held:
                    return memory.Outcome(1, ['another capture replay is running; requeue unchanged'], dict(requeued=0))
                result = capture_spool.requeue(path, inflight_path(env), memory.iso(_now(env)), accept)
            return memory.Outcome(0 if result['quarantined'] == 0 else 1,
                [f"requeued {result['requeued']}, {result['quarantined']} still quarantined; no network writes",
                 f"retention discarded {result['retention']['expired']} expired and {result['retention']['capacity']} oldest archives",
                 'restore the original endpoint/plane before retrying destination entries; expired/malformed entries cannot replay'], result)
        if clear:
            count = capture_spool.clear(path)
            return memory.Outcome(0, [f'cleared {count} quarantined captures'],
                                  dict(cleared=count, quarantined=0))
        rows = capture_spool.quarantined(path)
        return memory.Outcome(0, [f'{len(rows)} quarantined captures; --requeue retries locally, --clear discards them'],
            dict(quarantined=len(rows), limits=dict(bytes=capture_spool.MAX_QUARANTINE_BYTES,
                 entries=capture_spool.MAX_QUARANTINE_ENTRIES, age_days=capture_spool.MAX_QUARANTINE_AGE_DAYS), entries=[{k: row[k] for k in ('id', 'reason', 'at')} for row in rows]))
    except (OSError, ValueError, TypeError, KeyError):
        return memory.Outcome(1, ['capture quarantine UNKNOWN; inspect local files'], dict(quarantined=None))


def flush(client: quipu.Client | None = None, env=None, *, limit=100, deadline=None) -> memory.Outcome:
    """Quarantine unusable heads; retain uncertain RPCs for ordered retry.

    Each visited record/fragment counts against the replay budget, including
    expiry and refusal. Archive-before-ack keeps rejected evidence recoverable.
    """
    import time
    env = os.environ if env is None else env
    deadline = time.monotonic() + 10.0 if deadline is None else deadline
    done, visited, lines, local_error, rejected = 0, 0, [], False, []
    retention = dict(expired=0, capacity=0, bytes=0)
    path, prefix = spool_path(env), inflight_path(env)
    try:
        with _flush_lock(env) as held:
            if not held:
                return memory.Outcome(1, ['another capture flush is running'],
                                      dict(written=0, spooled=offline.capture_length(env)))
            retention = capture_spool.prune(path, memory.iso(_now(env)))
            while visited < limit and time.monotonic() < deadline:
                raw = capture_spool.next_head(path, prefix)
                if not raw:
                    break
                visited += 1
                entry, reason = _replay_entry(raw, env)
                if reason is None:
                    context = entry['_offline_context']
                    ttl = turtle(entry)
                if reason is None:
                    replay = client or quipu.Client(context['server'],
                        timeout=max(0.001, deadline - time.monotonic()), deadline=deadline,
                        provenance_env=context['env'])
                    if isinstance(replay, quipu.Client):
                        if replay.base != context['server']:
                            lines.append('stopped: client destination differs; capture retained')
                            break
                        replay.provenance_env = context['env']
                        replay.deadline = deadline
                    try:
                        replay.knot(ttl, context['plane_ns'] + planes.ROUTES['inferred'],
                                    entry['agent'], 'camayoc:cy capture')
                    except quipu.Refused:
                        reason = 'refused'
                    except quipu.QuipuError as exc:
                        lines.append(f'stopped: {type(exc).__name__}; uncertain capture retained')
                        break
                if reason:
                    capture_spool.quarantine(path, raw, reason, memory.iso(_now(env)), retention=retention)
                    if isinstance(entry, dict) and isinstance(entry.get('hash'), str):
                        rejected.append(entry['hash'])
                capture_spool.acknowledge(prefix, raw)
                if not reason:
                    done += 1
    except (OSError, ValueError, KeyError, TypeError) as exc:
        local_error = True
        lines.append(f'stopped: capture buffer {type(exc).__name__}; inspect acknowledgment state')
    rest = offline.capture_length(env)
    held = quarantine(env).data['quarantined']
    lines.append(f'captured {done}, {rest if rest is not None else "UNKNOWN"} still spooled, '
                 f'{held if held is not None else "UNKNOWN"} quarantined')
    if retention['expired'] or retention['capacity']:
        lines.append(f"quarantine retention discarded {retention['expired']} expired and {retention['capacity']} oldest archives")
    if held:
        lines.append('inspect: cy capture quarantine; retry locally: --requeue; discard: --clear')
    return memory.Outcome(0 if rest == 0 and held == 0 and not local_error else 1, lines,
                          dict(written=done, spooled=rest, quarantined=held, quarantined_hashes=rejected, quarantine_retention=retention))


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
            flush_detached(env)  # maintenance can quarantine expired/invalid heads
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
    if entry['hash'] in out.data.get('quarantined_hashes', []):
        return memory.Outcome(3, ['Not captured: quarantined locally; inspect `cy capture quarantine`.'],
                              dict(iri=iri, status='quarantined'))
    if out.data.get('spooled') is None:
        return memory.Outcome(3, ['Capture delivery UNKNOWN; inspect local spool before retrying.'],
                              dict(iri=iri, status='unknown'))
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
