"""Check imports in the interpreter that will execute RDF review commands."""
# arming: agent-invoked via just review-preflight/review-test; no unattended schedule.

from __future__ import annotations

import importlib
import importlib.metadata
import json
import sys


def main() -> int:
    dependencies = {
        "rdflib": "rdflib",
        "pyshacl": "pyshacl",
        "pytest": "pytest",
        "boto3": "boto3",
        "cryptography": "cryptography",
        "yaml": "PyYAML",
    }
    versions = {}
    missing = []
    for module, distribution in dependencies.items():
        try:
            importlib.import_module(module)
            versions[distribution] = importlib.metadata.version(distribution)
        except (ImportError, importlib.metadata.PackageNotFoundError):
            missing.append(distribution)
    if missing:
        print(
            f"RDF review preflight refused: {sys.executable} cannot import "
            f"{', '.join(missing)}. Run just review-preflight from the project root; "
            "do not treat skipped query tests as review success.",
            file=sys.stderr,
        )
        return 1
    print(json.dumps({"python": sys.executable, "version": sys.version, "dependencies": versions}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
