#!/usr/bin/env python3
"""Map CDEvents (CloudEvents envelope) to governed native graph records.

CDEvents (CD Foundation) is the deployment primitive: a deploy tool states
what happened in an open format, and Camayoc decides what it MEANS in the
graph. The mapping from CDEvents type to native class lives in
``mappings/cdevents.json``, which is reviewed data, so adding a tool is adding
a mapping, not code. Its sha256 rides in every episode's ``source``.

Input is one CloudEvent (JSON, ``data`` holding the CDEvent) or a bare CDEvent
(``context``/``subject``), from a file or stdin. By default the governed
``/episode`` body is printed. ``--post`` ensures the month's operational window
graph and sends the exact body through Camayoc's ingress helper.

Events are written into per-month operational WINDOW graphs (family
``deployment``), never into a knowledge plane. The record name is a digest of
the CloudEvent ``source`` + ``id``, the CloudEvents uniqueness key, so a
re-delivered event re-emits byte-identically.

An event this mapping cannot place, because the type is unknown, the spec
version unsupported or a required field absent, is QUARANTINED: a structured
``parked`` record and exit status 2, never a silent drop and never a guess.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import planes
import windows

SOURCE_KIND = "observed"
ROOT = Path(__file__).resolve().parents[1]
MAPPING_FILE = ROOT / "mappings" / "cdevents.json"
_TIME = re.compile(r"^(\d{4})-(\d{2})-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")
_TYPE = re.compile(r"^(dev\.cdevents\.[a-z]+\.[a-z]+)\.(\d+)\.\d+\.\d+$")


class Quarantined(ValueError):
    """The event cannot be mapped without guessing; it is parked, not dropped."""


def load_mapping(path: Path = MAPPING_FILE) -> tuple[dict, str]:
    raw = path.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def _get(obj: object, dotted: str) -> object:
    for part in dotted.split("."):
        if not isinstance(obj, dict) or part not in obj:
            return None
        obj = obj[part]
    return obj


def envelope(payload: object) -> tuple[dict, dict]:
    """Return (CloudEvent attributes, CDEvent) from either accepted shape."""
    if not isinstance(payload, dict):
        raise Quarantined("event is not a JSON object")
    if "specversion" in payload:
        cdevent = payload.get("data")
        if isinstance(cdevent, str):
            try:
                cdevent = json.loads(cdevent)
            except ValueError as exc:
                raise Quarantined(f"CloudEvent data is not JSON: {exc}") from None
        attrs = {k: payload.get(k) for k in ("specversion", "id", "source", "type", "time")}
    elif "context" in payload and "subject" in payload:
        cdevent = payload
        ctx = payload["context"] if isinstance(payload["context"], dict) else {}
        # A bare CDEvent maps onto the CloudEvents binding attribute for attribute.
        attrs = {"specversion": "1.0", "id": ctx.get("id"), "source": ctx.get("source"),
                 "type": ctx.get("type"), "time": ctx.get("timestamp")}
    else:
        raise Quarantined("neither a CloudEvent (specversion) nor a CDEvent (context/subject)")
    if not isinstance(cdevent, dict) or not isinstance(cdevent.get("context"), dict):
        raise Quarantined("CDEvent body missing (no context)")
    for key in ("id", "source", "type", "time"):
        if not isinstance(attrs.get(key), str) or not attrs[key].strip():
            raise Quarantined(f"CloudEvent attribute '{key}' missing")
    if attrs["type"] != cdevent["context"].get("type"):
        raise Quarantined("CloudEvent type and CDEvent context.type disagree")
    return attrs, cdevent


def episode_for(payload: object, *, actor: str, mapping: dict | None = None,
                mapping_sha: str | None = None) -> dict:
    """The governed /episode body for one CDEvent. Raises Quarantined."""
    if mapping is None or mapping_sha is None:
        mapping, mapping_sha = load_mapping()
    attrs, cdevent = envelope(payload)
    if attrs["specversion"] not in mapping["spec"]["cloudevents"]:
        raise Quarantined(f"CloudEvents specversion {attrs['specversion']!r} unsupported")
    version = str(cdevent["context"].get("version") or "")
    if not any(version == v or version.startswith(v + ".") for v in mapping["spec"]["cdevents"]):
        raise Quarantined(f"CDEvents spec version {version!r} unsupported")
    m = _TYPE.match(attrs["type"])
    if not m:
        raise Quarantined(f"type {attrs['type']!r} is not a versioned CDEvents type")
    base, major = m.group(1), m.group(2)
    rule = mapping["types"].get(base)
    if rule is None:
        raise Quarantined(f"no mapping for CDEvents type {base!r}")
    if major not in rule["versions"]:
        raise Quarantined(f"{base} major version {major} not mapped")
    t = _TIME.match(attrs["time"])
    if not t:
        raise Quarantined(f"time {attrs['time']!r} is not RFC 3339")

    values: dict[str, str] = {}
    for name, spec in rule["fields"].items():
        value = _get(cdevent, spec["path"])
        if value is None or (isinstance(value, str) and not value.strip()):
            if spec.get("required"):
                raise Quarantined(f"required field {name} ({spec['path']}) absent")
            continue
        if not isinstance(value, str):
            raise Quarantined(f"field {name} ({spec['path']}) is not a string")
        values[name] = value

    outcome = rule["outcome"]
    override = rule.get("outcomeWhenFalse")
    if override and _get(cdevent, override["path"]) is False:
        outcome = override["outcome"]

    key = hashlib.sha256(f"{attrs['source']}\0{attrs['id']}".encode()).hexdigest()[:16]
    name = f"deployment-event-{key}"
    service = values.pop("service")
    properties = {
        "sourceKind": SOURCE_KIND,
        "deploymentOutcome": outcome,
        "occurredAt": attrs["time"],
        "cdeventsType": attrs["type"],
        "cloudEventSource": attrs["source"],
        "cloudEventId": attrs["id"],
        **values,
    }
    label = f"{service} {outcome} {values['artifactId']} on {values['environment']}"
    return {
        "name": f"cdevent:{key}",
        "graph": windows.window_iri(rule["window"], f"{t.group(1)}-{t.group(2)}"),
        "episode_body": f"CDEvent {attrs['type']} for {service}",
        "source": f"cdevents:mapping={mapping_sha[:16]}:{attrs['source']}#{attrs['id']}",
        "actor": actor,
        "nodes": [{
            "name": name,
            "type": rule["class"],
            "description": label,
            "properties": properties,
        }],
        "edges": [{"source": name, "target": service, "relation": "deployedService"}],
    }


def window_of(body: dict) -> tuple[str, str]:
    """(family, YYYY-MM) of the window graph an episode targets."""
    graph = body["graph"]
    if not graph.startswith(windows.WINDOW_NS):
        raise Quarantined(f"episode graph {graph!r} is not a window graph")
    family, month = graph[len(windows.WINDOW_NS):].rsplit("/", 1)
    return family, month


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", nargs="?", type=Path, help="event JSON file; stdin when omitted")
    ap.add_argument("--actor", required=True)
    ap.add_argument("--post", action="store_true", help="POST the episode to Quipu")
    args = ap.parse_args()

    raw = args.input.read_text() if args.input else sys.stdin.read()
    try:
        payload = json.loads(raw)
        body = episode_for(payload, actor=args.actor)
        if args.post:
            family, month = window_of(body)
            windows.ensure_window(family, month, body["nodes"][0]["properties"]["occurredAt"])
            result = planes._post("/episode", body, client="camayoc-ingress")
        else:
            result = body
    except Exception as exc:
        # One bad event must not head-of-line block a spool. The caller gets a
        # structured park record and a non-zero status, then can continue.
        kind = "quarantined" if isinstance(exc, (Quarantined, ValueError)) else "parked"
        print(json.dumps({"outcome": kind, "input": str(args.input or "-"), "error": str(exc)}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
