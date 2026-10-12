"""Restore a proven frozen source journal into a new archive-only writer DB."""
import hashlib
import json
import os
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

from app.raw_archive.runner import destination_from_environment, source_settings

ROOT = Path('/var/lib/greenmind/archive/catalog-preparation-20261009/checkpoint-20261010T192239131864')
STATE = Path('/var/lib/greenmind/archive-copy-20261010')


def main():
    assert os.getuid() == 999 and os.uname().nodename == 'ubuntu-4gb-fsn1-1'
    assert not Path('/etc/greenmind/archive-copy-owner.json').exists()
    assert os.environ['RAW_ARCHIVE_DELETE_ENABLED']=='false'
    proof = json.loads((ROOT/'journal-proof.json').read_text())
    stage = json.loads((ROOT/'stage/stage.json').read_text())
    assert stage['archive_integrity_checked'] is True
    assert stage['source_sha256'] == proof['sha256']
    source = ROOT/'source-ledger.sqlite3'
    destination = STATE/'archive.sqlite3'
    assert not destination.exists()
    started = time.monotonic()

    def check():
        available = next(int(x.split()[1]) for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:'))/1024
        assert available >= 800 and os.getloadavg()[0] <= 2
        assert time.monotonic()-started < 110

    check()
    assert hashlib.file_digest(source.open('rb'),'sha256').hexdigest() == proof['sha256']
    box = destination_from_environment()
    try:
        binding = {'namespace':'production', 'destination':box.identity,
                   'sources':{k:source_settings(k,'production')['identity'] for k in ('gateway','direct')}}
    finally:
        box.close()
    original = sqlite3.connect(source.as_uri()+'?mode=ro',uri=True,timeout=2)
    original.execute('PRAGMA query_only=ON');original.execute('PRAGMA cache_size=-2048')
    assert json.loads(original.execute("SELECT value FROM backup_meta WHERE name='binding'").fetchone()[0]) == binding
    fd=os.open(destination,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
    db=sqlite3.connect(destination,timeout=2)
    db.execute('PRAGMA cache_size=-2048')
    original.backup(db,pages=128,progress=lambda *args:check(),sleep=0.01)
    db.execute('PRAGMA query_only=ON')
    interrupted = []
    def progress():
        try:check();return 0
        except Exception:interrupted.append(True);return 1
    db.set_progress_handler(progress,1000)
    assert db.execute('PRAGMA integrity_check').fetchall()==[('ok',)]
    tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert tables=={'archive','backup_meta','backup_work'}
    counts={name:db.execute('SELECT count(*) FROM '+name).fetchone()[0] for name in sorted(tables)}
    assert counts['archive']==proof['rows']
    assert all(original.execute('SELECT count(*) FROM '+name).fetchone()[0]==counts[name] for name in tables)
    assert db.execute('SELECT name,value FROM backup_meta ORDER BY name').fetchall()==original.execute('SELECT name,value FROM backup_meta ORDER BY name').fetchall()
    db.close();original.close();check()
    result={'complete':True, 'source_journal_sha256':proof['sha256'], 'tables':counts,
            'binding':binding, 'source_original_retained':True, 'restored_at':datetime.now(UTC).isoformat(),
            'empty_database_restore':True, 'integrity_checked':True, 'deleted_files':0}
    with (STATE/'journal-restored.json').open('x') as out:
        os.fchmod(out.fileno(),0o600);json.dump(result,out)
    print(json.dumps({'status':'journal_restored','tables':counts,'deleted_files':0}))


if __name__ == '__main__':
    try:main()
    except Exception as error:
        print(json.dumps({'status':'blocked','error_type':type(error).__name__,'deleted_files':0}))
        raise SystemExit(1) from None
