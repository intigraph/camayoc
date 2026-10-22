#!/usr/bin/env python3
"""GitHub change emitter: pull-request lifecycle as CDEvents, for DORA lead time.

    changes.py --repo owner/name --since 2026-10-15T00:00:00Z --spool DIR

Reads pull requests updated since a time through ONE GitHub GraphQL query per
page (the `gh` CLI holds the credential; nothing here reads a token) and spools
one CloudEvents 1.0 file per lifecycle step:

  change.created       the pull request opened
  testSuiteRun.finished  the head commit's REQUIRED checks all passed (pass) or
                       one failed (fail); pending or partial runs emit nothing
  change.reviewed      a forge review, or a review verdict comment (below)
  change.merged        merged, naming the merge commit
  change.abandoned     closed without merging

The emitter only states what happened. Lead time and its decomposition are
stored Quipu queries over the records Camayoc maps these events to.

"Green" means every check the base branch REQUIRES concluded success, skipped
or neutral, at the latest of their completion times. A rollup that is green
because only two path-filtered checks ran is not green, and a check that
finishes after the merge cannot make a change green before it.

Reviews: where reviewers share one forge account, a review is a COMMENT. A
comment counts as a review when its opening names a review or starts with a
verdict word, and the verdict is CHANGES (changes_requested) or APPROVE
(approved); the earlier word wins when both appear.

Each event's id is derived from the step (owner/repo#n/created, /merged,
/review/<id>, /ci/<sha>), so a re-run spools nothing new and a re-delivery
maps byte-identically. Only identifiers, times and verdict words leave the
forge: never comment bodies or check logs.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

SPEC = "0.4.1"
PASSING = {"SUCCESS", "SKIPPED", "NEUTRAL"}
# Anything else (PENDING, EXPECTED, CANCELLED, STALE) leaves the commit undecided.
FAILING = {"FAILURE", "ERROR", "TIMED_OUT", "STARTUP_FAILURE", "ACTION_REQUIRED"}
_VERDICT = re.compile(r"\b(APPROVED?|CHANGES(?: REQUESTED)?|REQUEST CHANGES)\b|\bChanges requested\b")
_REVIEW_OPENING = re.compile(r"\breview\b|^[*_`\s]*(?:SOURCE\s+)?(?:APPROVED?|CHANGES|REQUEST CHANGES|Changes requested)\b",
                             re.I | re.M)

QUERY = """
query($owner: String!, $name: String!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(first: 25, after: $after, orderBy: {field: UPDATED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number createdAt updatedAt closedAt mergedAt state
        mergeCommit { oid }
        reviews(first: 50) { nodes { fullDatabaseId state submittedAt commit { oid } } }
        comments(first: 100) { nodes { fullDatabaseId createdAt body } }
        commits(last: 50) { nodes { commit { oid
          statusCheckRollup { contexts(first: 100) { nodes {
            __typename
            ... on CheckRun { name conclusion completedAt }
            ... on StatusContext { context state createdAt } } } } } } }
      }
    }
  }
}
"""


def gh_graphql(variables: dict) -> dict:
    """One GraphQL call through the gh CLI; the credential never passes through here."""
    args = ["gh", "api", "graphql", "-f", f"query={QUERY}"]
    for key, value in variables.items():
        if value is not None:
            args += ["-F" if key == "after" else "-f", f"{key}={value}"]
    out = subprocess.run(args, capture_output=True, text=True, check=True, timeout=120).stdout
    return json.loads(out)


def required_checks(repo: str, branch: str) -> list[str]:
    out = subprocess.run(["gh", "api", f"repos/{repo}/branches/{branch}/protection/required_status_checks"],
                         capture_output=True, text=True, check=True, timeout=60).stdout
    return sorted(json.loads(out).get("contexts") or [])


def event(repo: str, number: int, step: str, kind: str, time: str, *,
          subject: dict | None = None, custom: dict | None = None) -> dict:
    change = f"{repo}#{number}"
    eid = f"{change}/{step}"
    source = f"github.com/{repo}/pulls"
    cdevent = {
        "context": {"version": SPEC, "id": eid, "source": source, "type": kind, "timestamp": time},
        "subject": subject or {"id": change, "source": source, "type": "change",
                               "content": {"repository": {"id": repo, "source": f"github.com/{repo}"}}},
    }
    if custom:
        cdevent["customData"] = custom
        cdevent["customDataContentType"] = "application/json"
    return {"specversion": "1.0", "id": eid, "source": source, "type": kind, "time": time,
            "subject": change, "datacontenttype": "application/json", "data": cdevent}


def ci_result(contexts: list[dict], required: list[str]) -> tuple[str, str] | None:
    """('pass'|'fail', time) for one commit's required checks, or None while undecided."""
    latest: dict[str, tuple[str, str]] = {}
    for ctx in contexts:
        if ctx.get("__typename") == "CheckRun":
            name, word, when = ctx.get("name"), ctx.get("conclusion"), ctx.get("completedAt")
        else:
            name, word, when = ctx.get("context"), ctx.get("state"), ctx.get("createdAt")
        if name in required and word and when:
            if name not in latest or when > latest[name][1]:
                latest[name] = (word, when)
    failed = [when for word, when in latest.values() if word in FAILING]
    if failed:
        return "fail", min(failed)
    if required and set(latest) == set(required) and all(w in PASSING for w, _ in latest.values()):
        return "pass", max(when for _, when in latest.values())
    return None


def verdict(body: str) -> str | None:
    """approved | changes_requested for a review comment, else None."""
    opening = "\n".join(body.strip().splitlines()[:3])
    match = _VERDICT.search(body)
    if not match or not _REVIEW_OPENING.search(opening):
        return None
    return "approved" if match.group(0).startswith("APPROVE") else "changes_requested"


def events_for(repo: str, pr: dict, required: list[str]) -> list[dict]:
    n = pr["number"]
    out = [event(repo, n, "created", "dev.cdevents.change.created.0.2.0", pr["createdAt"])]
    for node in pr["commits"]["nodes"]:
        commit = node["commit"]
        rollup = commit.get("statusCheckRollup") or {}
        result = ci_result(((rollup.get("contexts") or {}).get("nodes")) or [], required)
        if result:
            outcome, when = result
            out.append(event(repo, n, f"ci/{commit['oid']}", "dev.cdevents.testsuiterun.finished.0.2.0", when,
                             subject={"id": f"{repo}#{n}@{commit['oid']}", "source": f"github.com/{repo}/pulls",
                                      "type": "testSuiteRun",
                                      "content": {"outcome": outcome, "environment": {"id": "ci"}}},
                             custom={"changeId": f"{repo}#{n}", "changeRepository": repo, "commitSha": commit["oid"]}))
    for review in pr["reviews"]["nodes"]:
        if review.get("submittedAt") and review.get("state") != "PENDING":
            custom = {"reviewState": review["state"].lower()}
            if (review.get("commit") or {}).get("oid"):
                custom["commitSha"] = review["commit"]["oid"]
            out.append(event(repo, n, f"review/{review['fullDatabaseId']}", "dev.cdevents.change.reviewed.0.2.0",
                             review["submittedAt"], custom=custom))
    for comment in pr["comments"]["nodes"]:
        state = verdict(comment.get("body") or "")
        if state:
            out.append(event(repo, n, f"review/comment-{comment['fullDatabaseId']}",
                             "dev.cdevents.change.reviewed.0.2.0", comment["createdAt"],
                             custom={"reviewState": state}))
    if pr.get("mergedAt") and (pr.get("mergeCommit") or {}).get("oid"):
        out.append(event(repo, n, "merged", "dev.cdevents.change.merged.0.2.0", pr["mergedAt"],
                         custom={"commitSha": pr["mergeCommit"]["oid"]}))
    elif pr.get("state") == "CLOSED" and pr.get("closedAt"):
        out.append(event(repo, n, "abandoned", "dev.cdevents.change.abandoned.0.2.0", pr["closedAt"]))
    return out


def pull_requests(repo: str, since: str, fetch=gh_graphql):
    """Pull requests updated at or after `since`, newest first, one page per call."""
    owner, name = repo.split("/", 1)
    after = None
    while True:
        page = fetch({"owner": owner, "name": name, "after": after})["data"]["repository"]["pullRequests"]
        for pr in page["nodes"]:
            if pr["updatedAt"] < since:
                return
            yield pr
        if not page["pageInfo"]["hasNextPage"]:
            return
        after = page["pageInfo"]["endCursor"]


def spool(ev: dict, directory: Path) -> Path | None:
    """Write atomically; an event already spooled (or delivered) is left alone."""
    directory.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", ev["id"])
    path = directory / f"{stem}.json"
    if path.exists() or (directory / "delivered" / path.name).exists():
        return None
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps(ev, sort_keys=True, separators=(",", ":")) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True, help="owner/name")
    ap.add_argument("--since", required=True, help="RFC 3339 UTC; pull requests updated at or after it")
    ap.add_argument("--spool", type=Path, required=True)
    ap.add_argument("--branch", default="main", help="base branch whose required checks define green")
    args = ap.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repo):
        ap.error("--repo must be owner/name")
    required = required_checks(args.repo, args.branch)
    if not required:
        print(f"changes: REFUSED, {args.repo}:{args.branch} requires no checks; green is undefined", file=sys.stderr)
        return 2
    counts = {"prs": 0, "spooled": 0, "present": 0}
    for pr in pull_requests(args.repo, args.since):
        counts["prs"] += 1
        for ev in events_for(args.repo, pr, required):
            counts["spooled" if spool(ev, args.spool) else "present"] += 1
    print("changes: " + " ".join(f"{k}={v}" for k, v in counts.items()) + f" required={len(required)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
