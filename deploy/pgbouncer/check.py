"""Read-only bounded PgBouncer acceptance; no source records modified."""
import concurrent.futures
import json
import os
import sys
import time
from pathlib import Path

import psycopg2
from sqlalchemy.engine import make_url
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

TRACE = {'stage': 'preflight', 'settings': []}


def env(path):
    return dict(l.split('=', 1) for l in Path(path).read_text().splitlines() if l and not l.startswith('#'))


def main():
    assert os.geteuid() == 0
    archive = os.uname().nodename == 'ubuntu-4gb-fsn1-1'
    assert archive or os.uname().nodename == 'Biolingo'
    reserve = 896 if archive else 768
    available = int(next(x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:'))) / 1024
    assert available >= reserve and os.getloadavg()[0] <= (2 if archive else 2.4)
    root = Path('/etc/greenmind/pgbouncer-20261011' if archive else '/etc/greenmind/pgbouncer-fallback-20261011')
    values = env('/etc/greenmind/archive-reader-20261010/read.env' if archive else '/home/traver/greenmind-admin-production/1b449e9/application.env')
    def connect(key, options=None):
        u = make_url(values[key]).set(host='127.0.0.1', port=6432)
        dsn = u.render_as_string(hide_password=False).replace('postgresql+psycopg2:', 'postgresql:')
        kw = {'connect_timeout': 3}
        if options is not None: kw['options'] = options
        return psycopg2.connect(dsn, **kw)
    result = {'at': time.time(), 'host': 'archive' if archive else 'source', 'available_mib': available,
              'deleted_files': 0, 'tests': {}, 'private_only': True}
    # No writes: verify the same begin hook used by the private applications.
    if archive:
        from app.transaction_pool import attach_read_guards
        os.environ['GREENMIND_TRANSACTION_POOL_READS'] = 'true'
        metadata = env('/etc/greenmind/archive-copy-20261010/copy.env')

    def guarded_engine(key, *, metadata_role=False, timeout=3000):
        selected = metadata if metadata_role else values
        configured_key = ('RAW_ARCHIVE_DIRECT_DATABASE_URL' if key == 'DIRECT_DATABASE_URL' else 'RAW_ARCHIVE_GATEWAY_DATABASE_URL') if metadata_role else key
        u = make_url(selected[configured_key]).set(host='127.0.0.1', port=6432)
        engine = create_engine(u, poolclass=NullPool, connect_args={
            'connect_timeout': 3,
            'options': '-c default_transaction_read_only=on -c statement_timeout=3000 '
                       '-c lock_timeout=150 -c work_mem=8192 -c jit=off',
        })
        attach_read_guards(engine, statement_ms=timeout, lock_ms=150,
                           work_mem_kib=8192, idle_ms=5000 if metadata_role else None)
        return engine

    def read(index):
        key = 'DIRECT_DATABASE_URL' if archive and index % 2 else 'DATABASE_URL'
        TRACE['stage'] = 'transaction_guard_checks'
        if archive:
            metadata_role = '--reader-only' not in sys.argv and bool(index % 4 >= 2)
            engine = guarded_engine(key, metadata_role=metadata_role)
            try:
                with engine.begin() as db:
                    pid, role, statement, lock, readonly, work, idle = db.exec_driver_sql(
                        "SELECT pg_backend_pid(),current_user,current_setting('statement_timeout'),"
                        "current_setting('lock_timeout'),current_setting('transaction_read_only'),"
                        "current_setting('work_mem'),current_setting('idle_in_transaction_session_timeout')"
                    ).one()
                    expected_role = 'greenmind_wav_metadata_b4ae52d674aa' if metadata_role else 'greenmind_archive_reader_33fe395'
                    assert role == expected_role and statement == '3s' and lock == '150ms'
                    assert readonly == 'on' and work == '8MB'
                    if metadata_role: assert idle == '5s'
                    db.exec_driver_sql('SELECT pg_sleep(0.05)')
                    return key + ':' + role, pid
            finally:
                engine.dispose()
        db = connect(key)
        try:
            with db, db.cursor() as cur:
                cur.execute('SET LOCAL transaction_read_only=on')
                cur.execute("SELECT pg_backend_pid(),current_user,current_setting('statement_timeout'),current_setting('lock_timeout'),current_setting('transaction_read_only'),current_setting('work_mem')")
                pid, role, statement, lock, readonly, work = cur.fetchone()
                TRACE['settings'].append({'database_key':key,'statement_timeout':statement,'lock_timeout':lock,'readonly':readonly,'work_mem':work})
                assert readonly == 'on'
                assert role == 'admin'
                cur.execute('SELECT pg_sleep(0.05)')
                return key, pid
        finally: db.close()
    workers = 1 if '--sequential' in sys.argv else (6 if archive or '--parallel' in sys.argv else 1)
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        rows = list(executor.map(read, range(12 if archive or workers > 1 else 2)))
    TRACE['stage'] = 'pool_bounds'
    result['tests']['transactions'] = len(rows)
    result['tests']['concurrent_workers'] = workers
    result['tests']['reader_only'] = '--reader-only' in sys.argv
    pids = {key: len({pid for k,pid in rows if k == key}) for key in {k for k,_ in rows}}
    assert all(n <= (1 if archive else 3) for n in pids.values())
    result['tests']['backend_pid_counts'] = pids
    if archive:
        TRACE['stage'] = 'transaction_isolation'
        # Begin limits must replace previous client values on reused backends.
        for timeout in (1000, 3000, 8000, 1000):
            engine = guarded_engine('DATABASE_URL', timeout=timeout)
            try:
                with engine.begin() as db:
                    assert db.exec_driver_sql("SELECT current_setting('statement_timeout'),current_setting('transaction_read_only')").one() == (f'{timeout // 1000}s', 'on')
            finally: engine.dispose()
        result['tests']['transaction_guards_and_client_isolation'] = True
        TRACE['stage'] = 'timeout_rollback'
        engine = guarded_engine('DATABASE_URL', timeout=50)
        try:
            from sqlalchemy.exc import DBAPIError
            try:
                with engine.begin() as db: db.exec_driver_sql('SELECT pg_sleep(0.15)')
                raise AssertionError('Statement guard not enforced')
            except DBAPIError as exc:
                assert isinstance(exc.orig, psycopg2.errors.QueryCanceled)
            with engine.begin() as db:
                assert db.exec_driver_sql('SELECT 1').one() == (1,)
                assert db.exec_driver_sql("SELECT current_setting('statement_timeout')").scalar_one() == '50ms'
        finally: engine.dispose()
        result['tests']['timeout_and_rollback_recovery'] = True
    TRACE['stage'] = 'statistics_authorization'
    stats = psycopg2.connect(host='127.0.0.1', port=6432, dbname='pgbouncer', user='greenmind_pool_stats', password=(root / 'stats-secret').read_text(), connect_timeout=3)
    try:
        stats.autocommit = True
        with stats.cursor() as cur:
            cur.execute('SHOW POOLS'); names = [x[0] for x in cur.description]
            pools = [dict(zip(names,row)) for row in cur.fetchall()]
            result['pools'] = [{k:p.get(k) for k in ('database','user','cl_active','cl_waiting','sv_active','sv_idle','pool_mode')} for p in pools if p['database'] != 'pgbouncer']
            assert sum(p.get('sv_active',0) + p.get('sv_idle',0) for p in pools) <= (4 if archive else 3)
            try:
                cur.execute('RELOAD')
                raise AssertionError('Statistics identity has administration rights')
            except psycopg2.Error: result['tests']['stats_cannot_administer'] = True
    finally: stats.close()
    result['passed'] = True
    with (root / ('checks-' + str(time.time_ns()) + '.json')).open('x') as out:
        os.fchmod(out.fileno(),0o600); json.dump(result,out)
    print(json.dumps(result))


if __name__ == '__main__':
    try: main()
    except Exception as exc:
        print(json.dumps({'passed': False, 'error_type': type(exc).__name__, 'sqlstate': getattr(exc, 'pgcode', None), 'trace': TRACE, 'deleted_files': 0}))
        raise SystemExit(1)
