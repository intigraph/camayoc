#!/usr/bin/env python3
"""Package tracked Camayoc source; never include the working directory's dirt."""
# arming: ci Release workflow and ordinary packaging regression tests
import argparse
import gzip
import hashlib
import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args])


def package(ref, version, output):
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError("release version must be stable major.minor.patch")
    revision = git("rev-parse", "--verify", ref + "^{commit}").decode().strip()
    for file in (".claude-plugin/plugin.json", ".claude-plugin/marketplace.json"):
        metadata = json.loads(git("show", revision + ":" + file))
        versions = [metadata["version"]] if "version" in metadata else [p["version"] for p in metadata["plugins"]]
        if versions != [version]:
            raise ValueError("release version disagrees with " + file)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("output directory must be empty")
    prefix = "camayoc-v" + version
    source = git("archive", "--format=tar", "--prefix=" + prefix + "/", revision)
    archive = output / (prefix + ".tar.gz")
    archive.write_bytes(gzip.compress(source, mtime=0))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (output / "SHA256SUMS").write_text(digest + "  " + archive.name + "\n")
    provenance = {"repository": "https://github.com/scbrown/camayoc", "tag": "v" + version,
                  "version": version, "source_revision": revision,
                  "artifact": archive.name, "sha256": digest, "format": "git-archive-tar-gzip"}
    (output / "source.json").write_text(json.dumps(provenance, sort_keys=True, indent=2) + "\n")
    return provenance


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(json.dumps(package(args.ref, args.version, args.output), sort_keys=True))
