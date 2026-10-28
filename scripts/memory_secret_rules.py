#!/usr/bin/env python3
"""Secret refusal for agent-memory create tools (docs/design/agent-memory-entities.md §3).

The rules live in camayoc_cli/secret_rules.py so the installed `cy` carries
them; this module re-exports them for the repo scripts and tests.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from camayoc_cli.secret_rules import RULES, SECRET_REF, find_secrets, refusals  # noqa: E402,F401
