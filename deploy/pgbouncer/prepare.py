"""Prepare an inactive archive-host PgBouncer, preserving receivers and schedules."""
import hashlib
import json
import os
import secrets
import shutil
import subprocess
from pathlib import Path

from config import credentials, auth_file, config

ROOT = Path('/etc/greenmind/pgbouncer-20261011')
RUNTIME = Path('/opt/greenmind/pgbouncer-20261011')
UNIT = Path('/etc/systemd/system/greenmind-pgbouncer-20261011.service')
PACKAGE = Path('/home/traver/greenmind-pgbouncer-20261011/package/extracted')


def private(path, data):
    with path.open('xb') as out:
        os.fchmod(out.fileno(), 0o600)
        out.write(data)


def main():
    assert os.geteuid() == 0 and os.uname().nodename == 'ubuntu-4gb-fsn1-1'
    available = int(next(x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:'))) / 1024
    assert available >= 864 and os.getloadavg()[0] <= 2
    assert not ROOT.exists() and not RUNTIME.exists() and not UNIT.exists()
    paths = [Path('/etc/greenmind/archive-reader-20261010/read.env'), Path('/etc/greenmind/archive-copy-20261010/copy.env')]
    urls = []
    for path in paths:
        values = dict(l.split('=', 1) for l in path.read_text().splitlines() if l and not l.startswith('#'))
        assert values['RAW_ARCHIVE_DELETE_ENABLED'] == 'false'
        urls.extend(v for k,v in values.items() if k.endswith('DATABASE_URL'))
    passwords = credentials(urls)
    stats_secret = secrets.token_hex(32)
    ROOT.mkdir(mode=0o700)
    RUNTIME.mkdir(mode=0o755)
    shutil.copytree(PACKAGE, RUNTIME / 'package')
    for p in RUNTIME.rglob('*'):
        if p.is_file(): p.chmod(0o755 if p.name == 'pgbouncer' else 0o644)
    private(ROOT / 'auth.txt', auth_file(passwords, stats_secret).encode())
    private(ROOT / 'stats-secret', stats_secret.encode())
    private(ROOT / 'pgbouncer.ini', config().encode())
    text = '''[Unit]
Description=GreenMind bounded private archive database pool
After=greenmind-source-link.service
StartLimitIntervalSec=60
StartLimitBurst=3
[Service]
Type=simple
DynamicUser=yes
User=greenmind-db-pool
Slice=greenmind-archive.slice
LoadCredential=auth.txt:/etc/greenmind/pgbouncer-20261011/auth.txt
LoadCredential=pgbouncer.ini:/etc/greenmind/pgbouncer-20261011/pgbouncer.ini
Environment=LD_LIBRARY_PATH=/opt/greenmind/pgbouncer-20261011/package/usr/lib/x86_64-linux-gnu
ExecStart=/opt/greenmind/pgbouncer-20261011/package/usr/sbin/pgbouncer %d/pgbouncer.ini
MemoryMax=64M
MemorySwapMax=0
CPUQuota=10%
TasksMax=16
LimitNOFILE=256
UMask=0077
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
RestrictAddressFamilies=AF_UNIX AF_INET
IPAddressAllow=127.0.0.1/32
IPAddressDeny=any
CapabilityBoundingSet=
Restart=on-failure
RestartSec=5
[Install]
WantedBy=multi-user.target
'''
    with UNIT.open('x') as out: out.write(text)
    UNIT.chmod(0o644)
    subprocess.run(['systemd-analyze', 'verify', str(UNIT)], check=True, capture_output=True, timeout=10)
    subprocess.run(['systemctl', 'daemon-reload'], check=True, capture_output=True, timeout=15)
    proof = {'prepared_inactive': True, 'version': '1.25.1', 'listen': '127.0.0.1:6432', 'backend_maximum': 4,
             'read_roles': sorted(passwords), 'pg_roles_created': 0, 'new_identity': 'dynamic nonprivileged OS service and PgBouncer statistics-only account',
             'source_link_changed': False, 'receiver_changes': False, 'deleted_files': 0,
             'config_sha256': hashlib.sha256(config().encode()).hexdigest(),
             'binary_sha256': hashlib.sha256((RUNTIME / 'package/usr/sbin/pgbouncer').read_bytes()).hexdigest(),
             'unit_sha256': hashlib.sha256(UNIT.read_bytes()).hexdigest()}
    private(ROOT / 'prepared.json', json.dumps(proof).encode())
    print(json.dumps(proof))


if __name__ == '__main__': main()
