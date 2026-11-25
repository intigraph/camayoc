#!/usr/bin/env python3
"""Deterministic release asset checks: one fact per judgment (aegis-7zkl).

Two triggers, both events or event-armed: the release's own
``trigger_check_run`` completing (quipu release.yml job "Trusted repository
share", which waits for the pendant and is re-run after a late upload), or the backstop, published + ``backstop_seconds``, armed
by the release.published fact and used only when no check has been written.
Either writes ONE ReleaseAssetCheck fact: complete, or incomplete naming each
missing asset. It replaces a timer that re-observed the latest release every
five minutes.

    release_asset_check.py --repo intigraph/quipu --release-json FILE --actor A \
        (--trigger check-run --as-of COMPLETED_AT | --trigger backstop [--now EPOCH]) [--post]

``--release-json`` is the GitHub REST release object (``node_id``,
``tag_name``, ``published_at``, ``assets[].name``) the caller read once. The
mapping from repository to required assets is reviewed data
(``mappings/release-assets.json``); its sha256 rides on every fact, so a rule
change is visible on the record it produced.

Exit codes: 0 complete, 3 incomplete (the caller recruits a fix job), 4 too
early (a backstop before its deadline; nothing written, the arm is not
consumed), 2 quarantined (the input cannot be judged without guessing).

Each JUDGMENT is its own fact, keyed by release node id + rule digest +
trigger + the trigger's own instant (the check run's completed_at, or the
backstop deadline), never wall-clock now: re-running the same trigger writes
the same bytes, and a backstop judgment followed by a late check-run judgment
are two facts, not a conflict. A release's current verdict is its latest
judgment by occurredAt, read at query time. Assets are judged by exact name
only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from github_ingress import node_name
from ingest_cdevents import Quarantined, event_month

ROOT = Path(__file__).resolve().parents[1]
RULES_FILE = ROOT / "mappings" / "release-assets.json"
SOURCE_KIND = "observed"
WINDOW_FAMILY = "release-checks"


class TooEarly(Exception):
    """The release's deadline has not passed; judging now would guess."""


def load_rules(path: Path = RULES_FILE) -> tuple[dict, str]:
    raw = path.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def _epoch(value: str) -> float:
    event_month(value)  # calendar-valid RFC 3339, or Quarantined
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def judge(repo: str, release: object, rules: dict, *, trigger: str, as_of: str | None,
          now: float) -> tuple[str, list[str], dict]:
    """(outcome, missing assets, facts) for one release. Raises Quarantined or TooEarly."""
    rule = rules.get("repositories", {}).get(repo)
    if rule is None:
        raise Quarantined(f"no asset rule for {repo}")
    if not isinstance(release, dict):
        raise Quarantined("release is not a JSON object")
    node_id, tag, published = release.get("node_id"), release.get("tag_name"), release.get("published_at")
    for key, value in (("node_id", node_id), ("tag_name", tag), ("published_at", published)):
        if not isinstance(value, str) or not value.strip():
            raise Quarantined(f"release {key} missing")
    if release.get("draft") is True:
        raise Quarantined("draft release is not published")
    assets = release.get("assets")
    if not isinstance(assets, list) or not all(isinstance(a, dict) and isinstance(a.get("name"), str) for a in assets):
        raise Quarantined("release assets are not a list of named assets")
    published_at = _epoch(published)
    if trigger == "check-run":
        if not isinstance(as_of, str):
            raise Quarantined("a check-run trigger needs its completed_at")
        judged_at = _epoch(as_of)
        if judged_at < published_at:
            raise Quarantined("check run completed before the release was published")
        stamp = as_of
    elif trigger == "backstop":
        judged_at = published_at + int(rule["backstop_seconds"])
        if now < judged_at:
            raise TooEarly(f"backstop {int(judged_at)} not reached at {int(now)}")
        stamp = datetime.fromtimestamp(judged_at, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        raise Quarantined(f"unknown trigger {trigger!r}")
    present = {a["name"] for a in assets}
    required = [f"{rule['prefix']}{tag}{suffix}" for suffix in rule["required_suffixes"]]
    missing = [name for name in required if name not in present]
    outcome = "incomplete" if missing else "complete"
    return outcome, missing, {"node_id": node_id, "tag": tag, "stamp": stamp, "trigger": trigger}


def episode_for(repo: str, release: object, *, actor: str, trigger: str, as_of: str | None = None,
                now: float | None = None, rules: dict | None = None, rules_sha: str | None = None) -> dict:
    if rules is None or rules_sha is None:
        rules, rules_sha = load_rules()
    outcome, missing, facts = judge(repo, release, rules, trigger=trigger, as_of=as_of,
                                    now=time.time() if now is None else now)
    # One record per JUDGMENT, not per release: a backstop judgment and a later
    # check-run judgment of the same release are two facts, never two values on
    # one node. The latest judgment is a read-time question (max occurredAt).
    key = hashlib.sha256(
        f"{facts['node_id']}\0{rules_sha}\0{facts['trigger']}\0{facts['stamp']}".encode()).hexdigest()
    checked_at = facts["stamp"]
    name = f"release-asset-check-{key[:16]}"
    props: dict[str, object] = {
        "sourceKind": SOURCE_KIND,
        "assetCheckOutcome": outcome,
        "assetCheckKey": key,
        "assetRuleDigest": rules_sha,
        "assetCheckTrigger": facts["trigger"],
        # The trigger's own instant, not wall-clock now: a re-run of the same
        # trigger writes identical bytes.
        "occurredAt": checked_at,
    }
    if missing:
        props["missingAsset"] = missing
    release_node = node_name(facts["node_id"])
    import windows

    return {
        "name": f"release-check:{key[:16]}",
        "graph": windows.window_iri(WINDOW_FAMILY, checked_at[:7]),
        "episode_body": f"{repo} {facts['tag']} release assets {outcome}"
                        + (": missing " + ", ".join(missing) if missing else ""),
        "source": f"release-asset-check:rules={rules_sha[:16]}:{repo}#{facts['tag']}",
        "actor": actor,
        "nodes": [{"name": name, "type": "ReleaseAssetCheck",
                   "description": f"{repo} {facts['tag']} assets {outcome}", "properties": props}],
        "edges": [{"source": name, "target": release_node, "relation": "assetCheckOf"}],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--release-json", type=Path, required=True)
    ap.add_argument("--actor", required=True)
    ap.add_argument("--trigger", choices=("check-run", "backstop"), required=True)
    ap.add_argument("--as-of", help="check run completed_at (check-run trigger)")
    ap.add_argument("--now", type=float, default=None)
    ap.add_argument("--post", action="store_true")
    args = ap.parse_args(argv)
    try:
        body = episode_for(args.repo, json.loads(args.release_json.read_text()), actor=args.actor,
                           trigger=args.trigger, as_of=args.as_of, now=args.now)
    except TooEarly as exc:
        print(json.dumps({"outcome": "too-early", "reason": str(exc)}))
        return 4
    except (Quarantined, ValueError) as exc:
        print(json.dumps({"outcome": "quarantined", "error": str(exc)}))
        return 2
    if args.post:
        import planes
        import windows

        family, month = body["graph"][len(windows.WINDOW_NS):].rsplit("/", 1)
        windows.ensure_window(family, month, body["nodes"][0]["properties"]["occurredAt"])
        print(json.dumps(planes._post("/episode", body, client="camayoc-ingress"), sort_keys=True))
    else:
        print(json.dumps(body, sort_keys=True))
    return 3 if body["nodes"][0]["properties"]["assetCheckOutcome"] == "incomplete" else 0


if __name__ == "__main__":
    sys.exit(main())
