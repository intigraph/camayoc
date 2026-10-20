#!/usr/bin/env python3
"""Ship the example query library in a qpack, receive it, and ask it back.

The examples in `examples/` are stored queries, and the point of a stored query
is that it TRAVELS: quipu's share carries the queries it answers as a sealed
`queries.ttl` member (quipu aegis-fxpbys.2). This script is the dogfood — it
does exactly what a consumer of the library would do, with real binaries:

1. PRODUCER: load every setup's shapes and the fixture, register the example
   queries, and ask every case in `examples/cases.json`;
2. SHARE: `quipu share --queries <every example>` and check `queries.ttl`
   carries each one;
3. RECEIVER: a fresh store loads the same shapes (the setup recipe), imports
   the share under a pack namespace, promotes it, and asks every case again
   under the namespaced name.

It passes only if every receiver answer equals the producer's answer AND the
first column equals what `cases.json` expects — equality alone would pass two
stores that were both empty.

Exit codes: 0 all cases identical and expected; 1 a mismatch or a quipu error;
3 this quipu predates `queries.ttl` (the share carried no queries member), which
is a DEPENDENCY state, reported as itself rather than as a pass or a failure.

    scripts/examples_qpack.py --quipu PATH --quipu-server PATH [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
NAMESPACE = "camayoc-examples"
EXIT_MISSING_FEATURE = 3


class Failure(Exception):
    """A step that must not be read as success."""


class MissingFeature(Exception):
    """The quipu under test cannot carry queries in a share at all."""


def setups() -> dict:
    return json.loads((EXAMPLES / "setups.json").read_text())


def cases() -> list[dict]:
    return json.loads((EXAMPLES / "cases.json").read_text())


def definitions() -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted((EXAMPLES / "queries").glob("*.json"))]


def shape_files() -> list[Path]:
    """Every shape set any setup names, once, in a stable order."""
    seen: list[Path] = []
    for setup in setups().values():
        for rel in setup["shapes"]:
            path = ROOT / rel
            if path not in seen:
                seen.append(path)
    return seen


def cli(quipu: str, *args: str) -> str:
    done = subprocess.run([quipu, *args], capture_output=True, text=True, timeout=120)
    if done.returncode != 0:
        raise Failure(f"quipu {' '.join(args[:2])} exited {done.returncode}: {done.stderr.strip()}")
    return done.stdout


def load_shapes(quipu: str, db: Path) -> None:
    import seed_profile
    # The profile travels from its owner at a verified immutable revision,
    # never from a Camayoc copy. Temporary files belong only to this test store.
    with tempfile.TemporaryDirectory() as directory:
        for name, turtle in seed_profile.profiles(source=os.environ.get('CAMAYOC_SEEDS_SOURCE')):
            path = Path(directory)/(name+'.ttl')
            path.write_text(turtle)
            cli(quipu, 'shapes', 'load', 'seeds-'+name, str(path), '--db', str(db))
    for path in shape_files():
        cli(quipu, "shapes", "load", f"example-{path.stem}", str(path), "--db", str(db))


class Server:
    """An ephemeral quipu-server over one store file, for /queries and /ask."""

    def __init__(self, binary: str, db: Path):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        self.base = f"http://127.0.0.1:{port}"
        self.proc = subprocess.Popen(
            [binary, "--db", str(db), "--bind", f"127.0.0.1:{port}"],
            cwd=db.parent,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(100):
            try:
                self.request("/health")
                return
            except (OSError, urllib.error.URLError, Failure):
                time.sleep(0.1)
        self.close()
        raise Failure("ephemeral quipu-server did not start")

    def request(self, path: str, payload: dict | None = None) -> dict:
        data = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request(
            self.base + path,
            data=data,
            headers={"Content-Type": "application/json", "X-Quipu-Client": "camayoc-examples"},
            method="GET" if payload is None else "POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            raise Failure(f"{path} returned HTTP {exc.code}: {exc.read().decode(errors='replace')}") from exc

    def close(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


def ask_all(server: Server, prefix: str = "") -> list[dict]:
    answers = []
    for case in cases():
        result = server.request("/ask", {"name": prefix + case["query"], "params": case["params"]})
        answers.append({"columns": result["columns"], "rows": result["rows"]})
    return answers


def first_column(answer: dict) -> list:
    column = answer["columns"][0]
    return [row.get(column) for row in answer["rows"]]


def run(quipu: str, server_bin: str, work: Path) -> dict:
    producer = work / "producer.db"
    receiver = work / "receiver.db"
    share = work / "share"

    load_shapes(quipu, producer)
    cli(quipu, "knot", str(EXAMPLES / "fixtures" / "examples.ttl"), "--db", str(producer))
    names = [d["name"] for d in definitions()]
    with Server(server_bin, producer) as srv:
        for definition in definitions():
            srv.request("/queries", definition)
        before = ask_all(srv)

    # --destination internal: this is a local round trip between two scratch
    # stores, not a publication, so there is no outward catalogue to scrub by.
    args = ["share", "--output", str(share), "--destination", "internal", "--db", str(producer)]
    for name in names:
        args += ["--queries", name]
    cli(quipu, *args)
    member = share / "queries.ttl"
    if not member.is_file():
        raise MissingFeature()
    carried = sorted(
        line.split('"')[1]
        for line in member.read_text().splitlines()
        if line.strip().startswith("quipu:queryName")
    )
    if carried != sorted(names):
        raise Failure(f"queries.ttl carries {carried}, expected {sorted(names)}")
    manifest = json.loads((share / "manifest.json").read_text())

    load_shapes(quipu, receiver)
    staged = json.loads(
        cli(
            quipu, "import", str(share), "--db", str(receiver), "--destination", "internal",
            "--query-namespace", NAMESPACE, "--actor", "camayoc-examples",
        )
    )
    if staged["outcome"] != "staged":
        raise Failure(f"import was {staged['outcome']}: {staged['promotion']['blockers']}")
    expected = sorted(f"{NAMESPACE}/{n}" for n in names)
    # A staged import installs NOTHING: its queries wait behind the same promote gate
    # as its data (quipu >= 0.10.0, aegis-9ofqqs). Installing at import is the bug.
    early = sorted(staged["queries"].get("installed", []))
    if early:
        raise Failure(f"staged import already installed {early}; queries must wait for promote")
    waiting = sorted(staged["queries"].get("awaiting_promotion", []))
    if waiting != expected:
        raise Failure(f"import holds {waiting} for promotion, expected {expected}")
    promoted = json.loads(cli(quipu, "import", "promote", staged["share_id"], "--db", str(receiver)))
    installed = sorted(promoted["queries"]["installed"])
    if installed != expected:
        raise Failure(f"promote installed {installed}")
    with Server(server_bin, receiver) as srv:
        after = ask_all(srv, prefix=f"{NAMESPACE}/")

    report = []
    for case, there, here in zip(cases(), before, after):
        ok = there == here and first_column(here) == case["expect"]
        report.append({"query": case["query"], "params": case["params"], "rows": len(here["rows"]), "ok": ok})
    return {
        "share_id": manifest["share_id"],
        "queries_hash": manifest.get("queries_hash"),
        "queries": len(carried),
        "namespace": NAMESPACE,
        "cases": report,
        "ok": all(r["ok"] for r in report),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quipu", default=os.environ.get("QUIPU_BIN") or shutil.which("quipu"))
    ap.add_argument("--quipu-server", default=os.environ.get("QUIPU_SERVER_BIN") or shutil.which("quipu-server"))
    ap.add_argument("--json", action="store_true", help="print the full report as JSON")
    args = ap.parse_args(argv)
    if not all(b and Path(b).is_file() for b in (args.quipu, args.quipu_server)):
        print("examples_qpack: needs --quipu and --quipu-server (or QUIPU_BIN / QUIPU_SERVER_BIN)", file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        try:
            result = run(args.quipu, args.quipu_server, Path(tmp))
        except MissingFeature:
            print(
                "examples_qpack: this quipu wrote no queries.ttl — it predates qpack "
                "stored queries (quipu aegis-fxpbys.2). Not a pass, not a failure.",
                file=sys.stderr,
            )
            return EXIT_MISSING_FEATURE
        except Failure as exc:
            print(f"examples_qpack: FAIL: {exc}", file=sys.stderr)
            return 1
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for case in result["cases"]:
            mark = "ok  " if case["ok"] else "FAIL"
            print(f"{mark} {case['query']} {json.dumps(case['params'], sort_keys=True)} rows={case['rows']}")
        verdict = "identical" if result["ok"] else "MISMATCH"
        print(f"{result['queries']} queries shipped in {result['share_id']}; answers {verdict} after import")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
