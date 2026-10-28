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
    ("slack-token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}")),
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
_CANDIDATE = re.compile(r"[A-Za-z0-9+/_=-]{32,}")
ENTROPY_BITS = 4.2

SECRET_REF = re.compile(r"^[A-Za-z][A-Za-z0-9_.\-/]{1,127}$")


def _entropy(s: str) -> float:
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in Counter(s).values())


def _looks_random(tok: str) -> bool:
    classes = sum(bool(re.search(p, tok)) for p in ("[a-z]", "[A-Z]", "[0-9]"))
    return classes == 3 and _entropy(tok) >= ENTROPY_BITS


def find_secrets(text: str) -> list[str]:
    """Names of the rules that match ``text`` (empty when clean)."""
    hits = [name for name, rx in RULES if rx.search(text)]
    if not hits and any(_looks_random(t) for t in _CANDIDATE.findall(text)):
        hits.append("high-entropy-string")
    return hits


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
