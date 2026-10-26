"""Prepare private pooling for two inactive-route admin fallbacks only."""
import hashlib
import json
import os
import secrets
import shutil
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path('/etc/greenmind/pgbouncer-fallback-20261011')
RUNTIME = Path('/opt/greenmind/pgbouncer-fallback-20261011')
UNIT = Path('/etc/systemd/system/greenmind-pgbouncer-fallback-20261011.service')
CONFIGS = [Path('/home/traver/greenmind-admin-production') / x / 'application.env' for x in ('1b449e9', 'e148f83')]


def private(path, data):
    with path.open('xb') as out: os.fchmod(out.fileno(), 0o600); out.write(data)


def main():
    assert os.geteuid() == 0 and os.uname().nodename == 'Biolingo'
    available = int(next(x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:'))) / 1024
    assert available >= 704 and os.getloadavg()[0] <= 2.4
    assert not ROOT.exists() and not RUNTIME.exists() and not UNIT.exists()
    passwords = []
    for path in CONFIGS:
        values = dict(l.split('=', 1) for l in path.read_text().splitlines() if l and not l.startswith('#'))
        url = urlsplit(values['DATABASE_URL'])
        assert url.username == 'admin' and url.path == '/plantdb' and not url.query
        passwords.append(unquote(url.password))
    assert passwords[0] == passwords[1] and passwords[0] and not any(c in passwords[0] for c in '\r\n\x00')
    ROOT.mkdir(mode=0o700); RUNTIME.mkdir(mode=0o755)
    shutil.copytree(Path('/home/traver/greenmind-pgbouncer-20261011/package/extracted'), RUNTIME / 'package')
    for p in RUNTIME.rglob('*'):
        if p.is_file(): p.chmod(0o755 if p.name == 'pgbouncer' else 0o644)
    def quote(value): return '"' + value.replace('\\','\\\\').replace('"','\\"') + '"'
    stats = secrets.token_hex(32)
    private(ROOT / 'auth.txt', (quote('admin') + ' ' + quote(passwords[0]) + '\n' + quote('greenmind_pool_stats') + ' ' + quote(stats) + '\n').encode())
    private(ROOT / 'stats-secret', stats.encode())
    config = '''[databases]
plantdb = host=127.0.0.1 port=5432 dbname=plantdb pool_size=3 max_db_connections=3
[pgbouncer]
listen_addr = 127.0.0.1,172.28.20.1
listen_port = 6432
unix_socket_dir =
auth_type = scram-sha-256
auth_file = /run/credentials/greenmind-pgbouncer-fallback-20261011.service/auth.txt
stats_users = greenmind_pool_stats
pool_mode = transaction
max_client_conn = 32
default_pool_size = 3
min_pool_size = 0
reserve_pool_size = 0
max_db_connections = 3
max_user_connections = 3
server_idle_timeout = 5
server_lifetime = 300
server_connect_timeout = 3
server_login_retry = 2
query_wait_timeout = 5
client_login_timeout = 5
server_reset_query = DISCARD ALL
server_reset_query_always = 1
max_prepared_statements = 0
log_connections = 0
log_disconnections = 0
log_pooler_errors = 1
log_stats = 0
'''
    private(ROOT / 'pgbouncer.ini', config.encode())
    text = '''[Unit]
Description=GreenMind bounded old admin fallback connection pool
StartLimitIntervalSec=60
StartLimitBurst=3
[Service]
Type=simple
DynamicUser=yes
User=greenmind-db-fallback-pool
LoadCredential=auth.txt:/etc/greenmind/pgbouncer-fallback-20261011/auth.txt
LoadCredential=pgbouncer.ini:/etc/greenmind/pgbouncer-fallback-20261011/pgbouncer.ini
Environment=LD_LIBRARY_PATH=/opt/greenmind/pgbouncer-fallback-20261011/package/usr/lib/x86_64-linux-gnu
ExecStart=/opt/greenmind/pgbouncer-fallback-20261011/package/usr/sbin/pgbouncer %d/pgbouncer.ini
MemoryMax=64M
MemorySwapMax=0
CPUQuota=5%
TasksMax=16
LimitNOFILE=256
UMask=0077
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
RestrictAddressFamilies=AF_UNIX AF_INET
IPAddressAllow=127.0.0.1/32 172.28.20.0/24
IPAddressDeny=any
CapabilityBoundingSet=
Restart=on-failure
RestartSec=5
[Install]
WantedBy=multi-user.target
'''
    with UNIT.open('x') as out: out.write(text)
    UNIT.chmod(0o644)
    subprocess.run(['systemd-analyze','verify',str(UNIT)],check=True,capture_output=True,timeout=10)
    subprocess.run(['systemctl','daemon-reload'],check=True,capture_output=True,timeout=15)
    proof = {'prepared_inactive': True, 'version': '1.22.0', 'listen': ['127.0.0.1:6432', '172.28.20.1:6432'],
             'backend_maximum': 3, 'clients_planned': ['gm-admin-production-1b449e9-application-1', 'gm-admin-production-e148f83-application-1'],
             'existing_role_privileges_unchanged': True, 'new_pg_roles': 0, 'existing_role': 'admin', 'administrative_secrets_exported': False,
             'config_sha256': hashlib.sha256(config.encode()).hexdigest(), 'unit_sha256': hashlib.sha256(UNIT.read_bytes()).hexdigest(),
             'receiver_changes': False, 'nginx_changed': False, 'deleted_files': 0}
    private(ROOT / 'prepared.json', json.dumps(proof).encode()); print(json.dumps(proof))


if __name__ == '__main__': main()
