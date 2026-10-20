"""Read the exact Seeds-owned profile; never vendor it or choose another store."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / 'shapes/aegis/seeds-profile.json'
MAX_BYTES = 256 * 1024


def profiles(*, source=None, fetch=None):
    contract = json.loads(CONTRACT.read_text())
    if contract['repository'] != 'scbrown/seeds':
        raise ValueError('unexpected profile owner')
    if not re.fullmatch(r'[0-9a-f]{40}', contract['commit']):
        raise ValueError('profile source is not immutable')
    result = []
    for row in contract['profiles']:
        # The server's smaller admission subset reuses shape identifiers; do
        # not overlay it onto the full standalone validation profile.
        if row.get('role', 'full-profile') != 'full-profile':
            continue
        if row['path'] not in ('shapes/seeds.shapes.ttl', 'shapes/quipu-gate.shapes.ttl'):
            raise ValueError('unexpected Seeds profile path')
        if source is not None:
            root = Path(source).resolve()
            path = (root/row['path']).resolve()
            if not path.is_relative_to(root):
                raise ValueError('profile path escapes source')
            with path.open('rb') as stream:
                data = stream.read(MAX_BYTES + 1)
        else:
            url = ('https://raw.githubusercontent.com/scbrown/seeds/'
                   + contract['commit'] + '/' + row['path'])
            if fetch is not None:
                data = fetch(url)
            else:
                with urllib.request.urlopen(url, timeout=15) as response:
                    data = response.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES or hashlib.sha256(data).hexdigest() != row['sha256']:
            raise ValueError('Seeds source profile checksum mismatch')
        result.append((Path(row['path']).stem, data.decode()))
    if not result:
        raise ValueError('full Seeds profile missing')
    return result


def load(server):
    import quipu_auth
    # Resolve every byte before the first write. The normal installer supplies
    # authority; this module has no schedule, fallback credential or retry.
    rows = profiles(source=os.environ.get('CAMAYOC_SEEDS_SOURCE'))
    headers = {**quipu_auth.write_headers(server), 'X-Quipu-Client': 'camayoc-bootstrap'}
    for name, turtle in rows:
        request = urllib.request.Request(server.rstrip('/')+'/shapes',
            data=json.dumps({'action': 'load', 'name': 'seeds-'+name, 'turtle': turtle}).encode(),
            headers=headers)
        with urllib.request.urlopen(request, timeout=20) as response:
            outcome = json.load(response)
        if outcome.get('error') or outcome.get('conforms') is False:
            raise ValueError('Seeds profile load unproved; no retry')
    print('Seeds-owned source profiles submitted; installation smoke still required')


if __name__ == '__main__':
    load(sys.argv[1])
