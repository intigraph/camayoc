#!/usr/bin/env bash
set -euo pipefail
baseline=$(mktemp -d)
trap 'rm -rf "$baseline"' EXIT
git archive 93295fc | tar -x -C "$baseline"
printf 'Base 93295fc, same private fixture probe:\n'
python3 docs/demos/workitem-burndown/probe.py "$baseline"
printf '\nCandidate, private library fixture:\n'
python3 docs/demos/workitem-burndown/probe.py
