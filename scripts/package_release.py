#!/usr/bin/env python3
"""Package tracked Camayoc source; never include the working directory's dirt."""
# arming: ci Release workflow and ordinary packaging regression tests
import argparse
import gzip
import hashlib
import io
import json
import re
import subprocess
import tarfile
import tempfile
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args])


def compatibility_source(source, prefix):
    """Build the carried compatibility bundle from immutable archived files only."""
    marker = prefix + "/tests/fixtures/aegis-upstream/original-aegis-ontology.ttl"
    with tarfile.open(fileobj=io.BytesIO(source)) as archive:
        names = set(archive.getnames())
        if marker not in names:
            return source, None  # old revisions retain their original source-release format
        import rdflib
        if rdflib.__version__ != "7.6.0":
            raise ValueError("compatibility release compiler requires rdflib==7.6.0")
        with tempfile.TemporaryDirectory(prefix="camayoc-release-compat-") as temporary:
            root = Path(temporary)
            for member in archive.getmembers():
                relative = Path(member.name).relative_to(prefix)
                if str(relative).startswith(("shapes/aegis/", "tests/fixtures/aegis-upstream/")) or str(relative) == "scripts/build_aegis_compatibility.py":
                    if member.isdir():
                        continue
                    if not member.isfile() or ".." in relative.parts:
                        raise ValueError("compatibility source must contain only regular files")
                    path = root / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(archive.extractfile(member).read())
            output = root / "built"
            subprocess.run([sys.executable, str(root / "scripts/build_aegis_compatibility.py"),
                            "--source", str(root / "tests/fixtures/aegis-upstream/original-aegis-ontology.ttl"),
                            "--output", str(output)], check=True, capture_output=True, timeout=60)
            manifest = output / "manifest.json"
            proof = {"path": "compatibility/aegis/manifest.json",
                     "sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
                     "format": "aegis-compatibility-source/v1", "automatic_load": False}
            buffer = io.BytesIO(source)
            with tarfile.open(fileobj=buffer, mode="a") as expanded:
                for path in sorted(output.iterdir()):
                    name = prefix + "/compatibility/aegis/" + path.name
                    if name in names or not path.is_file() or path.is_symlink():
                        raise ValueError("compatibility output collision or non-file")
                    data = path.read_bytes()
                    info = tarfile.TarInfo(name)
                    info.size, info.mode, info.mtime = len(data), 0o644, 0
                    expanded.addfile(info, io.BytesIO(data))
            return buffer.getvalue(), proof


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
    source, compatibility = compatibility_source(source, prefix)
    archive = output / (prefix + ".tar.gz")
    # gzip.compress delegates to zlib in Python 3.11/3.12, which supplies a
    # platform-specific OS byte. GzipFile writes the neutral envelope itself.
    compressed = io.BytesIO()
    with gzip.GzipFile(fileobj=compressed, mode="wb", filename="", mtime=0, compresslevel=9) as bundle:
        bundle.write(source)
    archive.write_bytes(compressed.getvalue())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (output / "SHA256SUMS").write_text(digest + "  " + archive.name + "\n")
    provenance = {"repository": "https://github.com/scbrown/camayoc", "tag": "v" + version,
                  "version": version, "source_revision": revision,
                  "artifact": archive.name, "sha256": digest, "format": "git-archive-tar-gzip"}
    if compatibility is not None:
        provenance["compatibility"] = compatibility
    (output / "source.json").write_text(json.dumps(provenance, sort_keys=True, indent=2) + "\n")
    return provenance


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(json.dumps(package(args.ref, args.version, args.output), sort_keys=True))
