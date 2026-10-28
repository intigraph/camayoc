#!/usr/bin/env python3
"""Secret refusal for agent-memory create tools (docs/design/agent-memory-entities.md §3).

A memory record may say WHERE a secret lives (a secret-store key name in
``secretRef``), never the secret itself. Every caller-supplied field is
checked against named token rules plus an entropy rule for unlabeled long
strings. Diagnostics name the field and the rule, never the matched value.
"""
from __future__ import annotations

import math
import re
from collections import Counter

# Named rules: prefix- or structure-anchored, gitleaks-style.
RULES: list[tuple[str, re.Pattern[str]]] = [
    ("github-token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b")),
    ("github-fine-grained-pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b")),
    ("model-api-key", re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}")),
    ("stripe-key", re.compile(r"\b(?:sk|rk)_live_[A-Za-z0-9]{16,}\b")),
    ("slack-token", re.compile(r"\b(?:xox[abposr]|xapp)-[A-Za-z0-9-]{10,}")),
    ("slack-webhook", re.compile(r"hooks\.slack\.com/services/T[A-Z0-9]{6,}/B[A-Z0-9]{6,}/[A-Za-z0-9]{16,}")),
    ("npm-token", re.compile(r"\bnpm_[A-Za-z0-9]{36}\b")),
    ("huggingface-token", re.compile(r"\bhf_[A-Za-z0-9]{30,}\b")),
    ("discord-webhook", re.compile(r"discord(?:app)?\.com/api/webhooks/\d{15,22}/[A-Za-z0-9_-]{50,}")),
    ("discord-bot-token", re.compile(r"\b[MNO][A-Za-z0-9_-]{23,27}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,40}\b")),
    ("telegram-bot-token", re.compile(r"(?<![0-9])[0-9]{8,10}:[A-Za-z0-9_-]{35}(?![A-Za-z0-9_-])")),
    ("gitlab-token", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}")),
    ("aws-access-key-id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("private-key-block", re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----")),
    ("bearer-credential", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{20,}")),
    ("url-userinfo-password", re.compile(r"[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]{6,}@")),
    ("labeled-assignment", re.compile(
        r"(?i)\b(?:password|passwd|passphrase|secret|token|api[_-]?key|access[_-]?key)\b"
        r"\s*[:=]\s*['\"]?[^\s'\"]{8,}")),
]

# Unlabeled long strings: refused when they look random. Hex digests and git
# SHAs (one case, digits) are excluded by the mixed-class requirement.
#
# A locator is mostly paths and URLs, so the entropy rules never score a
# whole path as one token (review of #183): a URL's scheme, host and path are
# a location, not a credential, and are dropped before scoring (the named
# rules above still see the whole text, and the query string and fragment
# are still scored); any other path is scored one `/`-separated segment at a
# time. Base64 blobs, whose alphabet includes `/`, have their own rule.
_URL = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s\"'<>]+", re.I)
_CANDIDATE = re.compile(r"[A-Za-z0-9+/_=-]{32,}")
_BASE64 = re.compile(r"[A-Za-z0-9+/]{40,}={0,2}")
ENTROPY_BITS = 4.2

SECRET_REF = re.compile(r"^[A-Za-z][A-Za-z0-9_.\-/]{1,127}$")


def _entropy(s: str) -> float:
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in Counter(s).values())


def _classes(tok: str) -> int:
    return sum(bool(re.search(p, tok)) for p in ("[a-z]", "[A-Z]", "[0-9]"))


def _looks_random(tok: str) -> bool:
    return _classes(tok) == 3 and _entropy(tok) >= ENTROPY_BITS


def _without_url_locations(text: str) -> str:
    """Text with every URL replaced by only its query string and fragment."""
    def keep(m: re.Match) -> str:
        url = m.group(0)
        rest = re.split(r"[?#]", url, maxsplit=1)
        return " " + (re.sub(r"[?#&=]", " ", rest[1]) if len(rest) > 1 else "") + " "
    return _URL.sub(keep, text)


def _base64_blob(run: str) -> bool:
    core = run.rstrip("=")
    if not _looks_random(core.replace("/", "").replace("+", "")):
        return False
    if "+" in core or run.endswith("="):
        return True
    if "/" not in core:
        return False  # the plain entropy rule judges it
    # Only `/`: a blob splits into random-looking pieces, a path into words.
    segments = [x for x in core.split("/") if len(x) >= 3]
    return bool(segments) and 2 * sum(_classes(x) == 3 for x in segments) >= len(segments)


def find_secrets(text: str) -> list[str]:
    """Names of the rules that match ``text`` (empty when clean)."""
    hits = [name for name, rx in RULES if rx.search(text)]
    if hits:
        return hits
    rest = _without_url_locations(text)
    if any(_base64_blob(m.group(0)) for m in _BASE64.finditer(rest)):
        return ["base64-blob"]
    for token in _CANDIDATE.findall(rest):
        if any(len(seg) >= 32 and _looks_random(seg.strip("=")) for seg in token.split("/")):
            return ["high-entropy-string"]
    return []


def refusals(fields: dict[str, str]) -> list[str]:
    """Plain-word refusal lines for a create call; empty means accept."""
    out = []
    for field, value in fields.items():
        if value is None:
            continue
        value = str(value)
        if field == "secretRef":
            if not SECRET_REF.match(value) or find_secrets(value):
                out.append("Not recorded: secretRef must be a secret-store key name, not a value.")
            continue
        rules = find_secrets(value)
        if rules:
            out.append(
                f"Not recorded: {field} looks like a secret ({', '.join(rules)}); "
                "store the secret's key name in --secret-ref instead.")
    return out
