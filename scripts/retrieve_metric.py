#!/usr/bin/env python3
"""Execute a Camayoc metric retrieval method without storing its sample."""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


def execute(method: dict[str, Any], timeout: float = 5.0) -> dict[str, Any]:
    system = method.get("system")
    query = method.get("query")
    params = method.get("params") or {}
    if not system or not query:
        return {"status": "invalid", "error": "method requires system and query"}
    if system == "session_usage" and query == "work_cost":
        from work_cost import retrieve
        try:
            return {"status": "retrieved", "system": system, "result": retrieve(params)}
        except (ValueError, TypeError, KeyError) as exc:
            return {"status": "invalid", "system": system, "error": str(exc)}
    if system == "session_usage" and query == "work_cost_generation":
        from pathlib import Path
        import time
        from usage_generation_reader import consume
        if set(params) != {"producer", "harness", "session", "horizon", "consumer"} or params.get("consumer") not in ("st-cost", "brief"):
            return {"status":"invalid", "system":system, "error":"invalid generation reader scope"}
        # One persistent attempt fence per consumer, across every session and
        # generation. A caller cannot multiply rate by choosing new filenames.
        directory=Path(os.environ.get("CAMAYOC_COST_READ_STATE",Path.home()/".local/state/camayoc/cost-reads"))
        try:
            directory.mkdir(parents=True,exist_ok=True)
            value=consume(producer=params["producer"],harness=params["harness"],session=params["session"],
                          at=params["horizon"],state_path=directory/(params["consumer"]+".json"),now=time.time())
            return {"status":"retrieved" if value["status"]=="COMPLETE" else "unknown", "system":system, "result":value}
        except (ValueError,TypeError,KeyError,OSError):
            return {"status":"unknown", "system":system, "error":"generation validation unavailable"}
    if system != "prometheus":
        return {"status": "unsupported", "system": system}
    endpoint = params.get("endpoint")
    if not endpoint:
        return {"status": "unreachable", "system": system, "error": "no endpoint declared"}
    url = endpoint.rstrip("/") + "/api/v1/query?" + urllib.parse.urlencode({"query": query})
    request = urllib.request.Request(url)
    password = os.environ.get("PROMETHEUS_BASIC_AUTH_PASSWORD")
    if password:
        username = os.environ.get("PROMETHEUS_BASIC_AUTH_USER", "aegis")
        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        request.add_header("Authorization", f"Basic {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except (OSError, ValueError, urllib.error.HTTPError) as exc:
        return {"status": "unreachable", "system": system, "error": str(exc)}
    if payload.get("status") != "success":
        return {"status": "query_error", "system": system, "response": payload}
    return {"status": "retrieved", "system": system, "result": payload.get("data", {}).get("result", [])}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("method", help="JSON file containing system, query, params")
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()
    with open(args.method, encoding="utf-8") as handle:
        method = json.load(handle)
    result = execute(method, args.timeout)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "retrieved" else 2


if __name__ == "__main__":
    raise SystemExit(main())
