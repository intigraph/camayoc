"""Plane routing for cy writes: the same table as scripts/planes.py.

tests/test_cy_cli.py holds the two equal, so the installed CLI (which cannot
import scripts/) and the repo scripts route a sourceKind to the same graph.
"""
from __future__ import annotations

import os

ROUTES = {"observed": "crew/records", "declared": "crew/declared", "inferred": "crew/inferred"}


def plane_ns() -> str:
    return os.environ.get("CAMAYOC_PLANE_NS", "https://camayoc.local/plane/")


def plane_for(source_kind: str) -> str:
    if source_kind not in ROUTES:
        raise ValueError(f"no plane for sourceKind '{source_kind}'; refusing to default to ROOT")
    return plane_ns() + ROUTES[source_kind]


def iris() -> dict[str, str]:
    """Stored-query plane params: declared, records, inferred."""
    return {"declared": plane_for("declared"), "records": plane_for("observed"),
            "inferred": plane_for("inferred")}
