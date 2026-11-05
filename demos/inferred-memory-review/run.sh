#!/usr/bin/env bash
set -euo pipefail
base_dir=$(mktemp -d)
trap 'rm -rf "$base_dir"' EXIT
git archive 088c403d7541eca1acad9a112aa8ab35d5410134 scripts | tar -xf - -C "$base_dir"
printf 'Before: base088c403, overdue inferred memory plus non-memory control\n'
python3 docs/demos/inferred-memory-review/probe.py "$base_dir"
printf '\nAfter: candidate, identical isolated fixture\n'
python3 docs/demos/inferred-memory-review/probe.py . --require-fixed
