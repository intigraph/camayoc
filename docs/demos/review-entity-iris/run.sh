#!/usr/bin/env bash
set -euo pipefail
base_dir=$(mktemp -d)
trap 'rm -rf "$base_dir"' EXIT
git archive 135d92a95ba349e3c0662767fa624c09d30119f4 scripts | tar -xf - -C "$base_dir"
printf 'Before: base135d92a, three elapsed entity ages in an isolated graph\n'
python3 docs/demos/review-entity-iris/probe.py "$base_dir"
printf '\nAfter: candidate, identical fixture and controls\n'
python3 docs/demos/review-entity-iris/probe.py . --require-fixed
