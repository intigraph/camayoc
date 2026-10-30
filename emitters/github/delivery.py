#!/usr/bin/env python3
"""Convert one forge Actions payload into a merge/publication CDEvent.

No network reads or polling. The caller supplies the release tag's resolved
commit (GITHUB_SHA for a release workflow), never target_commitish as a branch.
Unmerged closes and unpublished/draft releases emit nothing.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from urllib.parse import quote


def event_for(kind: str, payload: dict, *, release_commit: str = "",
              forge: str = "github.com") -> dict | None:
    if not re.fullmatch(r"[A-Za-z0-9.-]+", forge):
        raise ValueError("forge must be a hostname")
    repo = payload["repository"]["full_name"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("invalid repository")
    source = f"https://{forge}/{repo}"
    if kind == "pull_request":
        pr = payload["pull_request"]
        if payload.get("action") != "closed" or pr.get("merged") is not True:
            return None
        number = pr["number"]
        if type(number) is not int or number < 1:
            raise ValueError("invalid pull request number")
        sha, when = pr["merge_commit_sha"], pr["merged_at"]
        actor = pr["merged_by"]["login"]
        eid = f"{repo}#{number}/merged"
        event_type = "dev.cdevents.change.merged.0.2.0"
        subject = {"id": f"{repo}#{number}", "source": source, "type": "change",
                   "content": {"repository": {"id": repo}}}
        custom = {"commitSha": sha, "eventActor": actor}
    elif kind == "release":
        release = payload["release"]
        if payload.get("action") != "published" or release.get("draft") is not False:
            return None
        sha, when = release_commit, release["published_at"]
        actor = payload["sender"]["login"]
        tag = release["tag_name"]
        if not isinstance(tag, str) or not tag.strip():
            raise ValueError("missing release tag")
        rid = release["id"]
        if type(rid) is not int or rid < 1:
            raise ValueError("invalid release id")
        eid = f"{repo}/release/{rid}/published"
        event_type = "dev.cdevents.artifact.published.0.2.0"
        subject = {"id": f"pkg:generic/{repo}@{quote(tag, safe='')}",
                   "source": source, "type": "artifact", "content": {"user": actor}}
        custom = {"commitSha": sha, "changeRepository": repo,
                  "releaseTag": tag, "eventActor": actor}
    else:
        raise ValueError("unsupported forge event")
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("a resolved full commit SHA is required")
    if not isinstance(actor, str) or not actor.strip():
        raise ValueError("the acting forge principal is required")
    # Use the same ingress parser for calendar validity; no receive-time fallback.
    from ingest_cdevents import event_month
    event_month(when)
    data = {"context": {"version": "0.4.1", "id": eid, "source": source,
                        "type": event_type, "timestamp": when},
            "subject": subject, "customData": custom,
            "customDataContentType": "application/json"}
    return {"specversion": "1.0", "id": eid, "source": source, "type": event_type,
            "time": when, "datacontenttype": "application/json", "data": data}


def main() -> int:
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=["pull_request", "release"])
    parser.add_argument("payload", type=Path)
    parser.add_argument("--release-commit", default="")
    parser.add_argument("--forge", default="github.com")
    args = parser.parse_args()
    try:
        event = event_for(args.kind, json.loads(args.payload.read_text()),
                          release_commit=args.release_commit, forge=args.forge)
    except (KeyError, TypeError, ValueError) as exc:
        print(f"delivery event refused: {exc}", file=sys.stderr)
        return 2
    if event is not None:
        print(json.dumps(event, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
