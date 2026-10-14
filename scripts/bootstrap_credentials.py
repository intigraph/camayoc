#!/usr/bin/env python3
"""Provision credentials only for a new loopback server owned by bootstrap."""
import json
import os
from pathlib import Path
import secrets
import socket
import tempfile
import sys
from urllib.parse import urlsplit

import quipu_auth


def provision(project: Path, server: str) -> None:
    endpoint = urlsplit(server)
    if (endpoint.scheme != 'http' or endpoint.hostname not in ('localhost', '127.0.0.1')
            or endpoint.username or endpoint.password or endpoint.path not in ('', '/')
            or endpoint.query or endpoint.fragment):
        raise ValueError('Bootstrap starts only a local loopback server; start or configure your remote server separately.')
    config = project / '.bobbin/config.toml'
    if config.parent.is_symlink():
        raise ValueError('Server configuration directory must not be a symlink.')
    if config.exists() or config.is_symlink():
        raise ValueError('Existing server configuration must be preserved.')
    if (project / '.quipu').exists() or (project / '.quipu').is_symlink():
        raise ValueError('Existing server data must be preserved.')
    # A failed HTTP probe is not proof of an unowned endpoint. Binding the
    # intended local address must succeed before provisioning a fresh server.
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', endpoint.port or 3030))
    value = quipu_auth.token()
    if not value:
        # An explicit but empty credential file is an operator configuration,
        # not permission to replace it with a newly generated identity.
        if os.environ.get('QUIPU_AUTH_TOKEN_FILE'):
            raise ValueError('Configured credential file is empty; repair it before bootstrap.')
        path = Path.home() / '.config/quipu/token'
        if path.exists() or path.is_symlink():
            raise ValueError('Existing canonical credential is empty; repair it before bootstrap.')
        if path.parent.is_symlink() or path.parent.parent.is_symlink():
            raise ValueError('Canonical credential directories must not be symlinks.')
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        value = secrets.token_urlsafe(32)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
        with os.fdopen(fd, 'w') as stream:
            stream.write(value + '\n')
    config.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    fd = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write('[quipu]\nstore_path = ".quipu/store.db"\n\n'
                     '[quipu.shacl]\nvalidate_on_write = true\n\n'
                     '[quipu.server]\n'
                     f'bind = {json.dumps("127.0.0.1:" + str(endpoint.port or 3030))}\n'
                     f'auth_token = {json.dumps(value)}\n')


def resolve() -> str:
    value = quipu_auth.token()
    if not value:
        raise ValueError(quipu_auth.HELP)
    return value


if __name__ == '__main__':
    try:
        if sys.argv[1] == 'provision':
            provision(Path(sys.argv[2]), sys.argv[3])
        elif sys.argv[1] == 'curl-config':
            value = resolve()
            fd, name = tempfile.mkstemp(prefix='camayoc-auth-')
            with os.fdopen(fd, 'w') as stream:
                stream.write('header = ' + json.dumps('Authorization: Bearer ' + value) + '\n')
            print(name)  # Only a private file path; never the bearer.

        else:
            raise ValueError('Unknown credential operation')
    except (OSError, UnicodeError, ValueError):
        print('Credential setup refused; preserve existing configuration and configure an accepted credential. ' + quipu_auth.HELP, file=sys.stderr)
        sys.exit(1)
