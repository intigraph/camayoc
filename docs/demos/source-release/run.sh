#!/bin/sh
set -eu
output=$(mktemp -d /tmp/camayoc-source-demo.XXXXXX)
trap 'rm -rf "$output"' EXIT
printf '%s\n' 'BEFORE: baseline has no source-release producer'
if git cat-file -e 731272ea0a5ed335f53cca35ce8fe0e3ab436823:scripts/package_release.py 2>/dev/null; then
  exit 1
else
  printf '%s\n' 'Producer absent at baseline (expected).'
fi
printf '%s\n' 'AFTER: package tracked revision, check source provenance and digest'
python3 scripts/package_release.py --ref HEAD --version 0.1.0 --output "$output"
(cd "$output" && sha256sum -c SHA256SUMS)
printf '%s\n' 'NEGATIVE CONTROL: mutate the archive; same checksum must reject'
printf '%s\n' 'tampered' >> "$output/camayoc-v0.1.0.tar.gz"
if (cd "$output" && sha256sum -c SHA256SUMS); then
  exit 1
else
  printf '%s\n' 'Tampered archive rejected (expected).'
fi
