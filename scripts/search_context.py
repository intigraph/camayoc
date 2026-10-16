"""Admit agent context, never diagnostic nearest-neighbor candidates."""
from __future__ import annotations

import math


class ContextUnknown(ValueError):
    """Missing, malformed or unsupported context evidence; not an abstention."""


def context_rows(raw: dict) -> list[dict]:
    """Require the versioned server decision plus finite admitted cosine values."""
    if not isinstance(raw, dict) or raw.get("error"):
        raise ContextUnknown("UNKNOWN search context")
    context = raw.get("context")
    if not isinstance(context, dict) or context.get("contract") != "quipu-context/v1":
        raise ContextUnknown("UNKNOWN search context contract")
    floor = context.get("similarity_floor")
    if isinstance(floor, bool) or not isinstance(floor, (int, float)) or not 0.52 <= floor <= 1 or not math.isfinite(floor):
        raise ContextUnknown("UNKNOWN search context floor")
    rows = raw.get("context_results")
    insufficient = raw.get("insufficient_context")
    if insufficient is True and context.get("outcome") == "NO CONTEXT":
        if not isinstance(rows, list) or rows:
            raise ContextUnknown("UNKNOWN negative context evidence")
        return []
    if insufficient is not False or context.get("outcome") != "CONTEXT":
        raise ContextUnknown("UNKNOWN search context outcome")
    if not isinstance(rows, list) or not rows:
        raise ContextUnknown("UNKNOWN admitted context")
    for row in rows:
        if not isinstance(row, dict):
            raise ContextUnknown("UNKNOWN context row")
        similarity = row.get("similarity")
        if isinstance(similarity, bool) or not isinstance(similarity, (int, float)) or not floor <= similarity <= 1 or not math.isfinite(similarity):
            raise ContextUnknown("UNKNOWN admitted similarity")
    return rows
