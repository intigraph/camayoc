"""The generated agent-memory tool definitions (scripts/memory_tools.py writes them)."""
from __future__ import annotations

import functools
import json
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"


@functools.lru_cache(maxsize=1)
def load() -> dict:
    return json.loads((DATA / "definitions.json").read_text(encoding="utf-8"))


def expand(curie: str) -> str:
    prefixes = load()["prefixes"]
    prefix, sep, local = curie.partition(":")
    return prefixes[prefix] + local if sep and prefix in prefixes else curie


def entity_names() -> list[str]:
    return sorted(load()["entities"])
