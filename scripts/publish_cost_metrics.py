#!/usr/bin/env python3
"""Publish st's rendered cost result through the existing Camayoc transport."""
import argparse
import json
from pathlib import Path
import re
import sys
from camayoc_metrics import push


SOURCE_MARKER = '# st-bead-cost-source '
SAMPLES = ('st_bead_tokens_total', 'st_bead_tokens_inferred_total',
           'st_bead_api_equivalent_usd', 'st_bead_api_equivalent_known_usd', 'st_bead_priced_token_share', 'st_bead_cost_coverage')


def publish(body, rig):
    """Partition source samples; the rig heartbeat must never replace them."""
    lines = body.splitlines()
    markers = [line[len(SOURCE_MARKER):] for line in lines if line.startswith(SOURCE_MARKER)]
    if not markers:
        return push('st_bead_cost', body, grouping={'rig': rig})
    try:
        source = json.loads(markers[0])
        if (len(markers) != 1 or not isinstance(source, dict)
                or set(source) != {'agent', 'harness', 'session'}
                or any(not isinstance(v, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', v)
                       for v in source.values())):
            raise ValueError('invalid identity')
    except (ValueError, TypeError):
        return False, 'invalid cost source identity; nothing pushed'
    samples, status = [], []
    for line in lines:
        if line.startswith(SOURCE_MARKER):
            continue
        target = samples if any(line.startswith((name + '{', name + ' ',
                    '# TYPE ' + name + ' ', '# HELP ' + name + ' ')) for name in SAMPLES) else status
        target.append(line)
        if line.startswith('st_bead_cost_last_success_timestamp_seconds '):
            samples.extend(('# TYPE st_bead_cost_source_last_success_timestamp_seconds gauge',
                line.replace('st_bead_cost_last_success_timestamp_seconds',
                             'st_bead_cost_source_last_success_timestamp_seconds', 1)))
    # PUT replaces only this parser session, so corrections remove that session's
    # old allocations while another session's samples remain independently visible.
    ok, why = push('st_bead_cost', '\n'.join(samples) + '\n', grouping={'rig': rig, **source})
    if not ok:
        return ok, why
    # The first source-aware tick also removes legacy samples from the rig-only
    # group. Leaving them there would double-count one session after migration.
    return push('st_bead_cost', '\n'.join(status) + '\n', grouping={'rig': rig})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('exposition', type=Path)
    parser.add_argument('--rig', required=True)
    args = parser.parse_args()
    ok, why = publish(args.exposition.read_text(), args.rig)
    print(why, file=sys.stderr)
    return 0 if ok else 2


if __name__ == '__main__':
    raise SystemExit(main())
