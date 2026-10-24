#!/usr/bin/env python3
"""Quarantine planes: register them, label them, and route writes by sourceKind.

This is camayoc-s0h's implementation. Until now the planes in
`docs/design/ingress.md` §2 were a table: an `inferred`-tagged node landed in
exactly the same graph an `observed` one did, and quarantine was skill
discipline plus a string.

**The ordering here is the whole point and it is not a preference.** Registering
the planes without labelling them produces separate graphs that every query
still reads at equal trust — the *appearance* of quarantine without the
substance, which is worse than none because it is unfalsifiable from the
outside. So `ensure_planes` does both or neither, and `plane_for` refuses to
route a write into a plane that has not been labelled.

Requires the quipu routes added for this bead: `POST /graph/create` and
`POST /graph/label`. Against an older quipu both 404 and this reports the plane
as UNAVAILABLE rather than silently writing to ROOT — the failure mode being
guarded against is precisely a routing layer that looks implemented and is not.

Usage:
    python3 scripts/planes.py ensure          # register + label all planes
    python3 scripts/planes.py status          # what exists, what is labelled
    python3 scripts/planes.py plane observed  # the graph IRI for a sourceKind
"""

from __future__ import annotations

try:
    import quipu_auth
except ModuleNotFoundError:
    from scripts import quipu_auth

import argparse
import json
import os
import re
import socket
import sys
import urllib.error
import urllib.request
import urllib.parse
from pathlib import Path

SERVER = os.environ.get("QUIPU_SERVER", "http://localhost:3030").rstrip("/")
# Explicit in-process override used by callers that load a selected token file.
# Ambient credentials are resolved for each request, including file rotation.
AUTH = None


def auth_token() -> str | None:
    """Resolve environment, explicit file and canonical credentials without legacy fallback."""
    try:
        return quipu_auth.token(AUTH) or None
    except (OSError, UnicodeError, ValueError):
        raise PlaneError("Unreadable or invalid bearer credential; " + quipu_auth.HELP) from None

#: The namespace planes live under. A parameter, never a hardcoded hostname —
#: camayoc's own convention (CLAUDE.md).
PLANE_NS = os.environ.get("CAMAYOC_PLANE_NS", "https://camayoc.local/plane/")

#: The trust chain these ranks are comparable within. A rank without its chain
#: is not comparable, which is why quipu's label API demands both.
TRUST_CHAIN = f"{PLANE_NS}chain/provenance"

#: The planes, exactly as `docs/design/ingress.md` §2 defines them.
#:
#: `rank` orders them within TRUST_CHAIN: higher is more trusted. The gap
#: between declared/records (30, 20) and inferred (0) is deliberate — a
#: promotion out of quarantine should have to cross visible distance, not
#: increment.
PLANES: dict[str, dict] = {
    "crew:records": {
        "iri": f"{PLANE_NS}crew/records",
        "contents": "task lifecycle, decisions, outcomes",
        "source_kinds": ["observed"],
        "rank": 20,
        "trust_iri": f"{PLANE_NS}trust/high",
        "data_kind": "knowledge",
    },
    "crew:declared": {
        "iri": f"{PLANE_NS}crew/declared",
        "contents": "conventions, directives, standing human decisions",
        "source_kinds": ["declared"],
        "rank": 30,
        "trust_iri": f"{PLANE_NS}trust/human-root",
        "data_kind": "knowledge",
    },
    "crew:inferred": {
        "iri": f"{PLANE_NS}crew/inferred",
        "contents": "model-written summaries, diagnoses, lessons",
        "source_kinds": ["inferred"],
        "rank": 0,
        "trust_iri": f"{PLANE_NS}trust/low",
        "data_kind": "knowledge",
    },
}

#: sourceKind -> plane key. The routing table, derived from PLANES so the two
#: cannot disagree.
ROUTES: dict[str, str] = {
    kind: name for name, spec in PLANES.items() for kind in spec["source_kinds"]
}


class PlaneError(RuntimeError):
    """A plane operation failed. Never swallowed into a silent ROOT write."""


_UNSAFE_HEADER = re.compile(r"[^\x21-\x7e ]")


def provenance_headers(client: str, env=None) -> dict[str, str]:
    """Structured write provenance (aegis-7zp4rc): X-Quipu-Agent, -Harness,
    -Model, -Session, -Host, filled from facts the environment holds, never
    typed by an agent. The same header contract as the stack's other writers:

    1. explicit QUIPU_AGENT / QUIPU_HARNESS / QUIPU_MODEL / QUIPU_SESSION / QUIPU_HOST;
    2. an agent session when one is running this script (SHANTY_AGENT,
       CLAUDECODE / CODEX_HOME, selected harness session ID, SHANTY_MODEL);
    3. otherwise the producer names itself: agent = its client label, harness = cron.

    Codex uses CODEX_SESSION_ID then CODEX_THREAD_ID; Claude uses
    CLAUDE_CODE_SESSION_ID. A field that cannot be filled is omitted, never guessed. Values are
    single-line printable ASCII, at most 128 characters: no header injection."""
    env = os.environ if env is None else env
    in_session = env.get("CLAUDECODE") == "1" or bool(env.get("CODEX_HOME"))
    harness = env.get("QUIPU_HARNESS") or (
        "claude" if env.get("CLAUDECODE") == "1" else "codex" if env.get("CODEX_HOME") else "cron")
    session = env.get("QUIPU_SESSION") or (
        (env.get("CODEX_SESSION_ID") or env.get("CODEX_THREAD_ID"))
        if harness == "codex" else env.get("CLAUDE_CODE_SESSION_ID"))
    raw = {
        "Agent": env.get("QUIPU_AGENT") or (env.get("SHANTY_AGENT") if in_session else None) or client,
        "Harness": harness,
        "Model": env.get("QUIPU_MODEL") or env.get("SHANTY_MODEL"),
        "Session": session,
        "Host": env.get("QUIPU_HOST") or socket.gethostname(),
    }
    out = {}
    for field, value in raw.items():
        text = _UNSAFE_HEADER.sub("", str(value or "")).strip()[:128]
        if text:
            out[f"X-Quipu-{field}"] = text
    return out


def _post(path: str, body: dict, *, client: str, form: bool = False,
          response_limit: int | None = None) -> dict:
    maximum_response = 8 * 1024 * 1024 if client == 'camayoc-cost-generation' else 262144
    if response_limit is not None and (type(response_limit) is not int or not 1 <= response_limit <= maximum_response):
        raise PlaneError("Invalid explicit response budget")
    req = urllib.request.Request(
        f"{SERVER}{path}",
        data=(urllib.parse.urlencode(body) if form else json.dumps(body, sort_keys=True)).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded" if form else "application/json", "X-Quipu-Client": client,
                 **provenance_headers(client)},
        method="POST",
    )
    is_write = path not in ("/query", "/search", "/search/nodes", "/search_nodes")
    if is_write:
        try:
            headers = quipu_auth.write_headers(SERVER, AUTH)
        except quipu_auth.CredentialRefused as exc:
            raise PlaneError(str(exc)) from None
        req.add_header("Authorization", headers["Authorization"])
    else:
        try:
            token = auth_token()
        except PlaneError:
            token = None  # Public reads stay available with a broken write credential.
        if token:
            req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read() if response_limit is None else r.read(response_limit + 1)
            if response_limit is not None and len(raw) > response_limit:
                raise PlaneError("Response exceeds explicit byte budget")
            return json.loads(raw or b"{}")
    except urllib.error.HTTPError as e:
        if e.code == 401 and is_write:
            e.close()
            raise PlaneError(str(quipu_auth.refuse(SERVER, "rejected (HTTP 401)"))) from None
        if e.code == 404:
            raise PlaneError(
                f"{path} returned 404 — this quipu has no plane routing. "
                "Writes must NOT fall back to ROOT: an unlabelled plane is "
                "quarantine's appearance without its substance."
            ) from e
        raise PlaneError(f"{path} failed: HTTP {e.code} {e.read()[:200]!r}") from e
    except urllib.error.URLError as e:
        # Distinguished from a refusal on purpose: "could not look" is not
        # "the plane is absent".
        raise PlaneError(f"{path} unreachable: {e.reason} — this is not evidence about the plane") from e
    except (TimeoutError, OSError) as e:
        # A timeout while READING the response is not a URLError, and used to
        # escape uncaught. For a write it is INDETERMINATE: the store may have
        # committed (aegis-4c3ppi chunk 02: client timeout, all 100 triples landed).
        raise PlaneError(
            f"{path} gave no answer ({type(e).__name__}: {e}); the request may have been "
            "applied — verify by reading back before retrying"
        ) from e


def ensure_planes(timestamp: str) -> list[dict]:
    """Register and label every plane. Both, or the plane is not usable.

    Returns one record per plane describing what happened, so a caller can
    tell a fresh registration from an existing one rather than inferring it
    from a bare success.
    """
    results = []
    for name, spec in PLANES.items():
        created = _post("/graph/create", {"graph": spec["iri"]}, client="camayoc-planes")
        labelled = _post(
            "/graph/label",
            {
                "graph": spec["iri"],
                "timestamp": timestamp,
                "trust": {
                    "iri": spec["trust_iri"],
                    "chain": TRUST_CHAIN,
                    "rank": spec["rank"],
                },
                # The dataKind axis (quipu graph-kinds-and-deep-freeze).
                # Ignored by an older quipu's strict parser? No — an older
                # /graph/label predates the key and DROPS unknown fields, and
                # a newer one parses it strictly. Either way the plane label
                # is written; `status` against a kind-aware store shows it.
                "kind": spec["data_kind"],
                "actor": "camayoc-planes",
            },
            client="camayoc-planes",
        )
        results.append(
            {
                "plane": name,
                "iri": spec["iri"],
                "g": created.get("g"),
                "newly_created": created.get("created"),
                "label_tx": labelled.get("tx_id"),
                "rank": spec["rank"],
            }
        )
    return results


def plane_for(source_kind: str, data_kind: str = "knowledge") -> str:
    """The graph IRI an episode with this `sourceKind` and `data_kind` earns.

    Routing is now 2-D (source_kind x data_kind), and the default is
    `"knowledge"` because every static plane above IS a knowledge plane —
    a documented fact about the table, not a silent fallback. Raises rather
    than defaulting on any pair with no plane: routing to ROOT would put
    model-written facts exactly where observed ones live — the single thing
    this whole mechanism exists to stop.

    `operational` data is deliberately NOT static-plane-addressed: it goes
    into time-windowed graphs so completed windows can be deep-frozen whole
    (`scripts/windows.py`, quipu graph-kinds-and-deep-freeze).
    """
    if data_kind == "operational":
        raise PlaneError(
            "operational data is window-addressed, not plane-addressed: use "
            "scripts/windows.py ensure_window(family, yyyymm) and write into "
            "the returned window graph. Static planes hold knowledge."
        )
    if data_kind != "knowledge":
        known = sorted({spec["data_kind"] for spec in PLANES.values()})
        raise PlaneError(
            f"no plane for dataKind '{data_kind}' (known plane kinds: {known}; "
            "'operational' routes via scripts/windows.py). Refusing to default "
            "to ROOT."
        )
    name = ROUTES.get(source_kind)
    if name is None:
        raise PlaneError(
            f"no plane for sourceKind '{source_kind}'. Known: {sorted(ROUTES)}. "
            "Refusing to default to ROOT."
        )
    return PLANES[name]["iri"]


def episode_body(name: str, turtle: str, source_kind: str, actor: str, source: str) -> dict:
    """An `/episode` body routed to the plane its `sourceKind` earns.

    `/episode` remains this helper's structured node/edge path. Current Quipu
    also permits arbitrary Turtle through `/knot` with a registered committed
    `graph`; the RML executor uses that path because generated predicates are
    not representable as episode edges.
    """
    return {
        "name": name,
        "graph": plane_for(source_kind),
        "episode_body": turtle,
        "source": source,
        "nodes": [],
        "edges": [],
        "actor": actor,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("ensure", help="register and label every plane")
    e.add_argument("--timestamp", default="2026-01-01T00:00:00Z")
    sub.add_parser("status", help="show the configured planes")
    pf = sub.add_parser("plane", help="the graph IRI for a sourceKind")
    pf.add_argument("source_kind")
    args = ap.parse_args()

    try:
        if args.cmd == "ensure":
            for r in ensure_planes(args.timestamp):
                state = "created" if r["newly_created"] else "already registered"
                print(f"{r['plane']:<16} {state:<18} rank={r['rank']:<3} tx={r['label_tx']}")
            print("\nAll planes registered AND labelled. Routing without labels "
                  "would be quarantine's appearance without its substance.")
        elif args.cmd == "status":
            for name, spec in PLANES.items():
                kinds = ",".join(spec["source_kinds"])
                print(f"{name:<16} rank={spec['rank']:<3} sourceKind={kinds:<10} {spec['iri']}")
        else:
            print(plane_for(args.source_kind))
    except PlaneError as exc:
        print(f"PLANE ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
