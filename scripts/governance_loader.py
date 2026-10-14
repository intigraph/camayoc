#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["rdflib==7.6.0"]
# ///
"""Explicit, offline governance resource staging; never contacts a server.

Only publishes a new local database after native verification and exact RDF
readback. No existing database is modified, and no policy is activated.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import tempfile
from urllib.parse import urlsplit

from rdflib import Graph, OWL, RDF, RDFS
from rdflib.compare import isomorphic


class Refused(Exception):
    """The stage could not establish the requested resource identity."""


def native(binary: str, *args: str) -> str:
    try:
        result = subprocess.run([binary, *args], capture_output=True, text=True,
                                timeout=60, check=False)
    except (OSError, subprocess.SubprocessError):
        raise Refused("native command unavailable or outcome UNKNOWN") from None
    if result.returncode:
        # Do not forward tool output: downstream errors can contain credentials.
        raise Refused(f"native {args[0]} refused; exit {result.returncode}")
    return result.stdout


def read_shapes(db: Path, name: str) -> Graph:
    try:
        with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            rows = conn.execute("SELECT name,turtle FROM shapes WHERE valid_to IS NULL").fetchall()
        if len(rows) != 1 or rows[0][0] != name:
            raise Refused("expected exactly the selected combined shape resource")
        return Graph().parse(data=rows[0][1], format="turtle")
    except (sqlite3.Error, ValueError):
        raise Refused("profile readback unreadable") from None


def verify(db: Path, binary: str, graph: str, shape: str,
           catalog: Graph, profile: Graph) -> int:
    raw = native(binary, "export", "--graph", graph, "--format", "turtle", "--db", str(db))
    try:
        observed = Graph().parse(data=raw, format="turtle")
    except Exception:
        raise Refused("catalog readback malformed or truncated") from None
    declarations = {(s, p, o) for s, p, o in catalog
                    if p == RDF.type and o in (RDF.Property, RDFS.Class)}
    if not declarations or not declarations.issubset(set(observed)):
        raise Refused("full direct declaration coverage missing")
    if not isomorphic(catalog, observed) or not isomorphic(profile, read_shapes(db, shape)):
        raise Refused("catalog or profile identity differs")
    return len(declarations)


def stage(*, artifact: Path, file_sha256: str, content_hash: str,
          version: str, graph: str, shape: str, output: Path,
          catalog_path: Path, profile_path: Path, catalog_sha256: str,
          profile_sha256: str, binary: str) -> dict:
    if not urlsplit(graph).scheme or graph == "urn:quipu:graph:root" or any(c.isspace() for c in graph):
        raise Refused("an explicit absolute nonROOT graph IRI is required")
    if not version or not shape or not re.fullmatch(r"sha256:[0-9a-f]{64}", content_hash):
        raise Refused("version, shape and content hash must be explicit")
    sources = [(artifact, file_sha256), (catalog_path, catalog_sha256),
               (profile_path, profile_sha256)]
    parent = output.parent.stat()
    if parent.st_uid != os.getuid() or parent.st_mode & 0o022:
        raise Refused("destination directory must be owned and not writable by others")
    if any(output.resolve() == path.resolve() for path, _ in sources):
        raise Refused("destination must be separate from every input")
    payloads = []
    for path, expected in sources:
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise Refused("each resource needs an exact SHA256")
        try:
            data = path.read_bytes()
        except OSError:
            raise Refused("resource missing or unreadable") from None
        if hashlib.sha256(data).hexdigest() != expected:
            raise Refused("resource byte identity differs")
        payloads.append(data)
    try:
        catalog = Graph().parse(data=payloads[1].decode(), format="turtle")
        profile = Graph().parse(data=payloads[2].decode(), format="turtle")
    except Exception:
        raise Refused("catalog or profile malformed") from None
    versions = {str(v) for subject in catalog.subjects(RDF.type, OWL.Ontology)
                for v in catalog.objects(subject, OWL.versionInfo)}
    if versions != {version}:
        raise Refused("catalog ontology version differs")
    # Owned byte copies avoid a caller changing the artifact between verify/unpack.
    with tempfile.TemporaryDirectory(prefix="camayoc-governance-", dir=output.parent) as temp:
        root = Path(temp)
        pinned = root / "resource.pendant.db"
        pinned.write_bytes(payloads[0])
        pinned.chmod(0o600)
        native(binary, "pack", "--verify", str(pinned))
        try:
            with closing(sqlite3.connect(pinned.as_uri() + "?mode=ro", uri=True)) as conn:
                rows = conn.execute("SELECT name,version,content_hash,counts FROM pack_manifest").fetchall()
            if len(rows) != 1 or rows[0][:3] != (shape, version, content_hash):
                raise Refused("pendant name/version/content hash differs")
            counts = json.loads(rows[0][3])
            if (counts.get("facts") != len(catalog) or counts.get("shapes") != 1
                    or counts.get("queries") != 0 or counts.get("vectors") != 0):
                raise Refused("resource must contain only the catalog and one combined profile")
        except (sqlite3.Error, ValueError, AttributeError):
            raise Refused("pendant manifest unreadable") from None
        if not isomorphic(profile, read_shapes(pinned, shape)):
            raise Refused("artifact profile differs from selected resource")
        staged = root / "staged.db"
        native(binary, "unpack", str(pinned), "--into", graph, "--db", str(staged))
        count = verify(staged, binary, graph, shape, catalog, profile)
        # Quipu opens SQLite in WAL mode. Consolidate only our new scratch
        # database after all native readers have exited; never copy a live WAL.
        with closing(sqlite3.connect(staged)) as conn:
            busy, _, _ = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if busy:
                raise Refused("scratch database checkpoint busy")
            if conn.execute("PRAGMA journal_mode=DELETE").fetchone()[0] != "delete":
                raise Refused("scratch database could not become standalone")
        # Refuse attached/sidecar-backed output: publishing only the main file
        # would otherwise hide an incomplete or composed store.
        if any(root.glob("staged.db-*")):
            raise Refused("native staging left database sidecars")
        staged.chmod(0o600)
        with staged.open("rb") as stream:
            os.fsync(stream.fileno())
        outcome = "staged"
        try:
            os.link(staged, output)  # atomic no-replace publication
        except FileExistsError:
            # Repeated staging proves presence; it does not pretend a new write.
            info = output.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o077 or any(output.parent.glob(output.name + "-*"))):
                raise Refused("existing destination is not a regular owned database")
            existing_bytes = output.read_bytes()
            observed_copy = root / "existing.db"
            observed_copy.write_bytes(existing_bytes)
            verify(observed_copy, binary, graph, shape, catalog, profile)
            current = output.lstat()
            identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
            if (output.read_bytes() != existing_bytes or identity(current) != identity(info)
                    or any(output.parent.glob(output.name + "-*"))):
                raise Refused("existing destination changed during readback")
            outcome = "verified-existing"
        directory_fd = os.open(output.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return {"outcome": outcome, "graph": graph, "shape": shape,
                "version": version, "direct_declarations": count,
                "catalog_triples": len(catalog), "profile_triples": len(profile),
                "activation": False, "server_acceptance": "NOT EVALUATED"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("artifact", "output", "catalog-path", "profile-path"):
        parser.add_argument("--" + name, required=True, type=Path)
    for name in ("file-sha256", "content-hash", "version", "graph", "shape",
                 "catalog-sha256", "profile-sha256", "binary"):
        parser.add_argument("--" + name, required=True)
    try:
        print(json.dumps(stage(**vars(parser.parse_args())), sort_keys=True))
        return 0
    except (Refused, OSError) as exc:
        print(json.dumps({"outcome": "REFUSED/UNKNOWN", "reason": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
