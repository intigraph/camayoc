#!/usr/bin/env python3
"""Bounded readback; build policy and credentials stay on the trusted host.

For a recorded owner alias, REPOSITORY_SHARE_STACK_MAP must name the operator's
existing stack-owner map. Missing or invalid declarations refuse the alias.
Literal repository matches do not need a map or an identity API lookup.
"""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
import time

spec = importlib.util.spec_from_file_location("publisher", Path(__file__).with_name("publish_repository_share.py"))
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)

# A non-literal receipt needs both the trusted stack migration map and GitHub's
# immutable numeric identity. Exact matches retain the existing offline contract.
REPOSITORY_ID = 1324782492


def declared_aliases(delivered, expected):
    selected = os.environ.get("REPOSITORY_SHARE_STACK_MAP")
    try:
        if not selected:
            raise ValueError("missing stack-owner map")
        raw = Path(selected).read_bytes()
        if len(raw) > 65536:
            raise ValueError("oversized stack-owner map")
        data = json.loads(raw)
        legacy, target = data["legacy_owner"], data["target_owner"]
        if (not all(isinstance(x, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", x)
                    for x in (legacy, target)) or legacy == target
                or not isinstance(data["repositories"], dict)):
            raise ValueError("invalid migration owners")
        canonical_owner = data["repositories"]["camayoc"]
        if canonical_owner not in (legacy, target):
            raise ValueError("invalid repository owner")
        aliases = {legacy + "/camayoc", target + "/camayoc"}
        if delivered not in aliases or expected not in aliases:
            raise ValueError("undeclared alias")
        return canonical_owner + "/camayoc"
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError("repository/source/tag/provenance mismatch: undeclared alias or unavailable stack-owner map") from None


def verify_repository_identity(delivered, expected):
    if not isinstance(delivered, str) or not isinstance(expected, str):
        raise ValueError("repository/source/tag/provenance mismatch: invalid repository")
    if delivered == expected:
        return
    canonical = declared_aliases(delivered, expected)
    for repository in sorted({delivered, expected}):
        try:
            result = subprocess.run(
                ["gh", "api", "repos/" + repository, "--jq", "{id,full_name}"],
                capture_output=True, text=True, timeout=20, check=True,
            )
            if len(result.stdout) > 65536:
                raise ValueError("oversized identity response")
            identity = json.loads(result.stdout)
        except (OSError, subprocess.SubprocessError, ValueError):
            raise ValueError("repository identity lookup unavailable; refusing verification") from None
        if (not isinstance(identity, dict) or type(identity.get("id")) is not int
                or identity["id"] != REPOSITORY_ID or identity.get("full_name") != canonical):
            raise ValueError("repository/source/tag/provenance mismatch: repository id or alias changed")


def verify(folder, tag, revision):
    if not re.fullmatch(r"v\d+\.\d+\.\d+(?:[.-][A-Za-z0-9.-]+)?", tag) or not re.fullmatch("[0-9a-f]{40}", revision):
        raise ValueError("invalid release identity")
    archive = folder / ("camayoc-" + tag + "-repository.pendant.tar.gz")
    sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    if archive.with_name(archive.name + ".sha256").read_text() != sha + "  " + archive.name + "\n":
        raise ValueError("archive checksum mismatch")
    proof = json.loads(archive.with_name(archive.name + ".provenance.json").read_text())
    expected = {
        "schema": "quipu.repository-share-producer/v1",
        "source_repository": publisher.repository_identity(),
        "source_revision": revision,
        "release_tag": tag,
        "archive_sha256": sha,
        "destination": "outward",
    }
    if any(proof.get(k) != v for k, v in expected.items() if k != "source_repository"):
        raise ValueError("repository/source/tag/provenance mismatch")
    verify_repository_identity(proof.get("source_repository"), expected["source_repository"])
    for key in ("quipu_sha256", "quipu_server_sha256", "bobbin_sha256"):
        if not re.fullmatch("[0-9a-f]{64}", proof.get(key, "")):
            raise ValueError("missing engine/indexer digest")
    if not re.fullmatch("[0-9a-f]{40}", proof.get("engine_source_revision", "")):
        raise ValueError("missing engine source pin")
    with tempfile.TemporaryDirectory(prefix="verify-pendant-") as temp:
        stage = Path(temp)
        with tarfile.open(archive) as tar:
            members = tar.getmembers()
            if len(members) != len({m.name for m in members}) or any(
                not m.isfile() or m.name not in publisher.MEMBERS for m in members
            ):
                raise ValueError("unexpected, duplicate, or unsafe archive member")
            # Every member is a flat allowlisted regular file; no path, link or device extraction.
            for member in members:
                (stage / member.name).write_bytes(tar.extractfile(member).read())
        publisher.check_share(stage)
    return expected


def wait(tag, revision, attempts=80, pause=time.sleep, run=subprocess.run):
    if not re.fullmatch(r"v\d+\.\d+\.\d+(?:[.-][A-Za-z0-9.-]+)?", tag) or not re.fullmatch("[0-9a-f]{40}", revision):
        raise ValueError("invalid release identity before lookup")
    repository = publisher.repository_identity()
    stem = "camayoc-" + tag + "-repository.pendant.tar.gz"
    last_read = "not attempted"
    for number in range(attempts):
        with tempfile.TemporaryDirectory(prefix="repository-pendant-readback-") as temp:
            folder = Path(temp)
            try:
                result = run(
                    [
                        "gh",
                        "release",
                        "download",
                        tag,
                        "--repo",
                        repository,
                        "--dir",
                        temp,
                        "--pattern",
                        stem,
                        "--pattern",
                        stem + ".sha256",
                        "--pattern",
                        stem + ".provenance.json",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=45,
                )
                last_read = "exit=" + str(result.returncode)
                if result.returncode == 0:
                    return verify(folder, tag, revision)
            except subprocess.TimeoutExpired:
                last_read = "read timeout"
        if number + 1 < attempts:
            pause(30)
    raise ValueError("repository pendant unverified after bounded trusted-producer wait; " + last_read)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: verify-repository-share.py <release-tag> <exact-release-sha>")
    print(json.dumps(wait(sys.argv[1], sys.argv[2])))
