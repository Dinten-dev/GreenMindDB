"""Bounded private transaction pooling for existing archive read identities."""
import re
from urllib.parse import unquote, urlsplit

DATABASES = {'plantdb', 'greenmind_direct_production'}
ROLES = {'greenmind_archive_reader_33fe395', 'greenmind_wav_metadata_b4ae52d674aa'}


def credentials(urls):
    result = {}
    seen = set()
    for url in urls:
        item = urlsplit(url)
        role, password = unquote(item.username or ''), unquote(item.password or '')
        database = unquote(item.path.lstrip('/'))
        if item.scheme not in {'postgresql', 'postgresql+psycopg2'} or item.hostname != '127.0.0.1' or item.port != 5432 or item.query or item.fragment:
            raise ValueError('Only existing private source-link connections are supported')
        if role not in ROLES or database not in DATABASES or not password or any(c in password for c in '\r\n\x00'):
            raise ValueError('Unexpected database identity')
        if role in result and result[role] != password:
            raise ValueError('Inconsistent password for existing identity')
        result[role] = password
        seen.add((role, database))
    if seen != {(r, d) for r in ROLES for d in DATABASES}:
        raise ValueError('Both named databases and identities are required')
    return result


def auth_file(passwords, stats_secret):
    if set(passwords) != ROLES or not re.fullmatch(r'[a-f0-9]{64}', stats_secret):
        raise ValueError('Invalid private authentication inventory')
    def quote(value):
        return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'
    return '\n'.join(quote(name) + ' ' + quote(secret) for name, secret in sorted({**passwords, 'greenmind_pool_stats': stats_secret}.items())) + '\n'


def config():
    # Four backend slots maximum: two per database and two per identity.
    # Startup safeguards are tracked, never silently discarded.
    return '''[databases]
plantdb = host=127.0.0.1 port=5432 dbname=plantdb pool_size=1 max_db_connections=2
greenmind_direct_production = host=127.0.0.1 port=5432 dbname=greenmind_direct_production pool_size=1 max_db_connections=2

[users]
greenmind_archive_reader_33fe395 = max_user_connections=2 pool_size=1
greenmind_wav_metadata_b4ae52d674aa = max_user_connections=2 pool_size=1

[pgbouncer]
listen_addr = 127.0.0.1
listen_port = 6432
unix_socket_dir =
auth_type = scram-sha-256
auth_file = /run/credentials/greenmind-pgbouncer-20261011.service/auth.txt
stats_users = greenmind_pool_stats
pool_mode = transaction
max_client_conn = 32
default_pool_size = 1
min_pool_size = 0
reserve_pool_size = 0
max_db_connections = 2
max_user_connections = 2
server_idle_timeout = 5
server_lifetime = 300
server_connect_timeout = 3
server_login_retry = 2
query_wait_timeout = 5
client_login_timeout = 5
idle_transaction_timeout = 0
server_reset_query = DISCARD ALL
server_reset_query_always = 0
track_extra_parameters = default_transaction_read_only,statement_timeout,lock_timeout,work_mem,jit,idle_in_transaction_session_timeout
max_prepared_statements = 0
log_connections = 0
log_disconnections = 0
log_pooler_errors = 1
log_stats = 0
'''
