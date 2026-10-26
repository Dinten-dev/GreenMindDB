"""Interactively configure known operator credentials; never discover/reset them."""

import argparse
import getpass
import hashlib
import json
import os
import pwd
import secrets
import stat
import subprocess
import sys
from pathlib import Path

CLIENT = Path('/home/traver/greenmind-operator-preparation-20261005-1055/mc')
CLIENT_SHA256 = '01f866e9c5f9b87c2b09116fa5d7c06695b106242d829a8bb32990c00312e891'


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    account = pwd.getpwnam('traver')
    if os.geteuid() != account.pw_uid or not sys.stdin.isatty():
        raise RuntimeError('Run interactively as traver, without sudo')
    if CLIENT.is_symlink() or not CLIENT.is_file():
        raise RuntimeError('Verified MinIO client unavailable')
    digest = hashlib.sha256()
    with CLIENT.open('rb') as body:
        for chunk in iter(lambda: body.read(65536), b''):
            digest.update(chunk)
    if digest.hexdigest() != CLIENT_SHA256:
        raise RuntimeError('MinIO client checksum differs')

    os.umask(0o077)
    parent = Path(account.pw_dir) / '.config' / 'greenmind'
    for path in (parent.parent, parent):
        if path.is_symlink():
            raise RuntimeError('Private configuration directory must not be a symlink')
        if not path.exists():
            path.mkdir(mode=0o700)
        if not path.is_dir() or path.stat().st_uid != account.pw_uid:
            raise RuntimeError('Private configuration owner differs')
    if stat.S_IMODE(parent.stat().st_mode) & 0o077:
        raise RuntimeError('GreenMind configuration directory must have mode 0700')

    print('Nur die vorhandene MinIO-Anmeldung eingeben. Kein neues Passwort setzen.')
    access = input('MinIO-Benutzername: ').strip()
    secret = getpass.getpass('MinIO-Passwort (unsichtbar): ')
    if not access or not secret or any(c in access + secret for c in '\r\n\0'):
        raise RuntimeError('Empty or malformed operator credentials')

    # Always use a new directory. An existing operator alias is never replaced.
    directory = parent / ('minio-operator-' + secrets.token_hex(8))
    directory.mkdir(mode=0o700)
    config = directory / 'config.json'
    with config.open('x') as body:
        os.fchmod(body.fileno(), 0o600)
        json.dump({'version': '10', 'aliases': {'greenmind-operator': {
            'url': 'http://127.0.0.1:9000', 'accessKey': access,
            'secretKey': secret, 'api': 'S3v4', 'path': 'auto',
        }}}, body)
        body.flush()
        os.fsync(body.fileno())
    del access, secret

    # Credentials are read from the private configuration, never process args.
    environment = {k: os.environ[k] for k in ('PATH', 'HOME', 'USER', 'LANG') if k in os.environ}
    result = subprocess.run(
        [str(CLIENT), '--config-dir', str(directory), '--json',
         'admin', 'info', 'greenmind-operator'],
        env=environment, capture_output=True, timeout=15,
    )
    if result.returncode:
        print('Anmeldung/Administratorprüfung nicht bestätigt. Keine Konten oder Dienste geändert.')
        print('Private Konfiguration: ' + str(config))
        return 1
    print('PASS: Betreiber-Alias eingerichtet; Administratorprüfung erfolgreich.')
    print('Konfigurationsverzeichnis: ' + str(directory))
    print('Keine Benutzer angelegt, keine Daten gelöscht, keine Dienste verändert.')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        # No exception/CLI details: these might contain operator credentials.
        print('BLOCKED: ' + type(error).__name__ + '; keine automatische Wiederholung.')
        raise SystemExit(1)
