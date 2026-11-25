#!/usr/bin/env python3
"""Map an Alertmanager webhook notification to governed alert records (aegis-dc5 G1).

Alertmanager states which alerts fire or resolve; Camayoc writes them into
Quipu as typed facts (shapes/alertmanager.shapes.ttl) so a reaction engine
can fire on the change stream and st can route by rule, instead of a bridge
polling chain.

Input is one webhook body (``version`` "4"), from a file or stdin. By default
the governed ``/episode`` bodies are printed, one per alert. ``--post`` ensures
each alert's monthly window graph (family ``alerts``) and sends the bodies
through Camayoc's ingress helper.

Each alert yields an AlertInstance (keyed by Alertmanager's fingerprint, only
facts fixed for that fingerprint) and one AlertTransition keyed by
fingerprint + status + startsAt + endsAt. Alertmanager re-sends a firing alert
every repeat interval with the same startsAt: the re-send maps to the SAME,
byte-identical records, so replay is idempotent. Nothing per-delivery (receive
time, group key, receiver) rides on a record. Label values are not stored; the
instance carries a digest of the canonical label set and the few labels
routing needs (alertname, severity, keeper, host).

An alert this mapping cannot place without guessing (unknown status, missing
or malformed fingerprint, impossible time, resolved without an end) is
QUARANTINED with a structured record and exit status 2, never dropped and
never coerced. One bad alert quarantines the whole notification so a partial
write can never pass for a complete one.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import windows
from ingest_cdevents import Quarantined, event_month

SOURCE_KIND = "observed"
WINDOW_FAMILY = "alerts"
SUPPORTED_VERSIONS = ("4",)
# Alertmanager's zero time: endsAt of an alert that has not resolved.
ZERO_TIME = "0001-01-01T00:00:00Z"
_FINGERPRINT = re.compile(r"^[0-9a-f]{16}$")
# Labels routing reads, in precedence order where several name the same fact.
_HOST_LABELS = ("host", "hostname", "instance")


def _string_labels(alert: dict) -> dict[str, str]:
    labels = alert.get("labels")
    if not isinstance(labels, dict) or not labels:
        raise Quarantined("alert has no labels")
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in labels.items()):
        raise Quarantined("alert labels are not all strings")
    return labels


def labels_digest(labels: dict[str, str]) -> str:
    """SHA-256 of the canonical label set: sorted ``key=value`` lines."""
    canonical = "".join(f"{k}={labels[k]}\n" for k in sorted(labels))
    return hashlib.sha256(canonical.encode()).hexdigest()


def transition_key(fingerprint: str, status: str, starts: str, ends: str) -> str:
    return hashlib.sha256(f"{fingerprint}\0{status}\0{starts}\0{ends}".encode()).hexdigest()


def _instance(fingerprint: str, labels: dict[str, str]) -> dict:
    name = labels.get("alertname", "").strip()
    if not name:
        raise Quarantined("alert has no alertname label")
    props: dict[str, str] = {
        "sourceKind": SOURCE_KIND,
        "alertFingerprint": fingerprint,
        "alertName": name,
        "alertLabelsDigest": labels_digest(labels),
    }
    for prop, label in (("severity", "severity"), ("keeper", "keeper")):
        if labels.get(label, "").strip():
            props[prop] = labels[label]
    host = next((labels[k] for k in _HOST_LABELS if labels.get(k, "").strip()), None)
    if host:
        props["alertHost"] = host
    where = f" on {host}" if host else ""
    return {"name": f"alert-{fingerprint}", "type": "AlertInstance",
            "description": f"{name}{where}", "properties": props}


def episode_for_alert(alert: object, *, actor: str) -> dict:
    """The governed /episode body for one alert of a notification. Raises Quarantined."""
    if not isinstance(alert, dict):
        raise Quarantined("alert is not a JSON object")
    fingerprint = alert.get("fingerprint")
    if not isinstance(fingerprint, str) or not _FINGERPRINT.match(fingerprint):
        raise Quarantined(f"fingerprint {fingerprint!r} is not 16 lowercase hex digits")
    status = alert.get("status")
    if status not in ("firing", "resolved"):
        raise Quarantined(f"alert status {status!r} is not firing or resolved")
    starts, ends = alert.get("startsAt"), alert.get("endsAt")
    event_month(starts)
    if status == "firing":
        # A firing alert's endsAt is either the zero time or Alertmanager's
        # predicted expiry; neither is a fact about this transition.
        occurred, ends_key = starts, ""
    else:
        if not isinstance(ends, str) or ends == ZERO_TIME:
            raise Quarantined("resolved alert has no endsAt")
        event_month(ends)
        occurred, ends_key = ends, ends
    month = event_month(occurred)
    labels = _string_labels(alert)
    instance = _instance(fingerprint, labels)
    key = transition_key(fingerprint, status, starts, ends_key)
    transition = {
        "name": f"alert-transition-{key[:16]}",
        "type": "AlertTransition",
        "description": f"{instance['description']} {status}",
        "properties": {
            "sourceKind": SOURCE_KIND,
            "alertStatus": status,
            "alertTransitionKey": key,
            "occurredAt": occurred,
        },
    }
    return {
        "name": f"alertmanager:{key[:16]}",
        "graph": windows.window_iri(WINDOW_FAMILY, month),
        "episode_body": f"Alertmanager {status} {instance['description']}",
        "source": f"alertmanager:fingerprint={fingerprint}:status={status}:startsAt={starts}",
        "actor": actor,
        "nodes": [instance, transition],
        "edges": [{"source": transition["name"], "target": instance["name"], "relation": "transitionOf"}],
    }


def episodes_for(payload: object, *, actor: str) -> list[dict]:
    """One /episode body per alert of a webhook notification. Raises Quarantined.

    Every alert is mapped before any body is returned: a notification is
    written whole or quarantined whole.
    """
    if not isinstance(payload, dict):
        raise Quarantined("notification is not a JSON object")
    version = payload.get("version")
    if version not in SUPPORTED_VERSIONS:
        raise Quarantined(f"Alertmanager webhook version {version!r} unsupported")
    alerts = payload.get("alerts")
    if not isinstance(alerts, list) or not alerts:
        raise Quarantined("notification carries no alerts")
    bodies = [episode_for_alert(alert, actor=actor) for alert in alerts]
    # Alertmanager groups distinct alerts; the same transition twice in one
    # notification is one record, kept once.
    unique: dict[str, dict] = {}
    for body in bodies:
        unique.setdefault(body["name"], body)
    return list(unique.values())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", nargs="?", type=Path, help="webhook JSON file; stdin when omitted")
    ap.add_argument("--actor", required=True)
    ap.add_argument("--post", action="store_true", help="POST the episodes to Quipu")
    args = ap.parse_args()

    raw = args.input.read_text() if args.input else sys.stdin.read()
    try:
        bodies = episodes_for(json.loads(raw), actor=args.actor)
    except (Quarantined, ValueError) as exc:
        print(json.dumps({"outcome": "quarantined", "input": str(args.input or "-"), "error": str(exc)}))
        return 2
    if not args.post:
        print(json.dumps(bodies, sort_keys=True))
        return 0
    import planes  # only the posting path needs credentials and the network

    results = []
    for body in bodies:
        family, month = body["graph"][len(windows.WINDOW_NS):].rsplit("/", 1)
        try:
            windows.ensure_window(family, month, body["nodes"][1]["properties"]["occurredAt"])
            results.append(planes._post("/episode", body, client="camayoc-ingress"))
        except Exception as exc:
            # A failed write is UNKNOWN, not absent: report what was sent and
            # stop, so the caller reads back before any retry.
            print(json.dumps({"outcome": "parked", "posted": len(results), "of": len(bodies),
                              "episode": body["name"], "error": str(exc)}))
            return 2
    print(json.dumps(results, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
