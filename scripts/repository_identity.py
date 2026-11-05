"""Resolve the release repository from hosted context or this checkout's origin."""

import os
from pathlib import Path
import re
import subprocess


def repository():
    identity = os.environ.get("GITHUB_REPOSITORY")
    if identity is None:
        remote = subprocess.check_output(
            ["git", "-C", str(Path(__file__).resolve().parents[1]), "remote", "get-url", "origin"],
            text=True, timeout=10,
        ).strip()
        match = re.fullmatch(r"(?:https://github\.com/|git@github\.com:)([^/]+/[^/]+?)(?:\.git)?", remote)
        if not match:
            raise ValueError("origin must identify a GitHub repository")
        identity = match.group(1)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", identity):
        raise ValueError("invalid GITHUB_REPOSITORY identity")
    return identity
