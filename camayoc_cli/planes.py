"""Plane routing for cy writes: the same table as scripts/planes.py.

tests/test_cy_cli.py holds the two equal, so the installed CLI (which cannot
import scripts/) and the repo scripts route a sourceKind to the same graph.
"""
from __future__ import annotations

import os

ROUTES = {"observed": "crew/records", "declared": "crew/declared", "inferred": "crew/inferred"}


def plane_ns() -> str:
    return os.environ.get("CAMAYOC_PLANE_NS", "https://camayoc.local/plane/")


#: Neutral public default for DEPLOYMENT INSTANCE IRIs this tool mints
#: (mem/<kind>/... record keys and versions, mem/topic/, mem/capture/). No
#: private homelab host is the default (aegis-55ya). A deployment that already
#: holds records sets CAMAYOC_INSTANCE_NS to its existing base so its keys keep
#: their IRIs; the aegis deployment sets it to LEGACY_INSTANCE_NS.
DEFAULT_INSTANCE_NS = "https://camayoc.local/id/"
#: FORMER-NAME ALIAS, read side only: the base every aegis record was minted
#: under before aegis-55ya. Readers also look here so an old-IRI record still
#: recalls and supersedes; nothing new is minted under it unless configured.
LEGACY_INSTANCE_NS = "http://aegis.gastown.local/ontology/"


def instance_ns() -> str:
    """The configured base for instance IRIs (CAMAYOC_INSTANCE_NS)."""
    return os.environ.get("CAMAYOC_INSTANCE_NS") or DEFAULT_INSTANCE_NS


def instance_bases() -> list[str]:
    """Bases readers accept: the configured one first, then the former name."""
    return list(dict.fromkeys([instance_ns(), LEGACY_INSTANCE_NS]))


def plane_for(source_kind: str) -> str:
    if source_kind not in ROUTES:
        raise ValueError(f"no plane for sourceKind '{source_kind}'; refusing to default to ROOT")
    return plane_ns() + ROUTES[source_kind]


def iris() -> dict[str, str]:
    """Stored-query plane params: declared, records, inferred."""
    return {"declared": plane_for("declared"), "records": plane_for("observed"),
            "inferred": plane_for("inferred")}
