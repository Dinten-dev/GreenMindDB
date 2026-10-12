"""Finite archive-host coordinator: complete parts, never deletion or routing."""
import hashlib
import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

RUNTIME = Path('/opt/greenmind/archive-preparation/20261010-v42')
BASE = Path('/var/lib/greenmind/archive/catalog-preparation-20261009')
LEDGER = BASE/'checkpoint-20261010T192239131864/stage'
GATEWAY = BASE/'ranges1024-20261010T184450/stage'
DIRECT = BASE/'capture-direct-20261010T1132/stage'
OUTPUT = BASE/'complete-parts-20261010T1945'
READ_PARENT = Path('/var/lib/greenmind/archive-reader-validation-20261009')
READ_OUTPUT = READ_PARENT/'complete-parts-20261010T1945'
REFERENCE = READ_PARENT/'complete-parts-20261010T1945-reference.json'
CONTROL = Path('/opt/greenmind/archive-validation/catalog-parts-20261010-v2')
PHASE = Path('/opt/greenmind/archive-validation/catalog-part-phase-20261010-v2.py')
PREPARED_SHA = 'ab0c5ebff3754edc33cd25c98e483480b2320738186403f4b5de38b732c58a7e'
PUBLISH_ENV = Path('/etc/greenmind/archive-catalog-publish-20261010.env')
READ_ENV = Path('/etc/greenmind/archive-catalog-read-20261010.env')


def write_new(path, value, uid=0, gid=0, mode=0o600):
    with path.open('xb') as out:
        os.fchmod(out.fileno(), mode)
        os.fchown(out.fileno(), uid, gid)
        out.write(value)


def phase(name, arguments, reader=False):
    account = 'greenmind-archive-read' if reader else 'greenmind-archive-copy'
    profile = READ_ENV if reader else PUBLISH_ENV
    for attempt in range(4):
        unit = 'greenmind-catalog-'+name+'-'+datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f').lower()
        props = {'User': account, 'Group': account, 'Slice': 'greenmind-archive.slice',
                 'EnvironmentFile': str(profile), 'MemoryMax': '384M', 'MemorySwapMax': '0',
                 'CPUQuota': '50%', 'Nice': '19', 'IOSchedulingClass': 'idle',
                 'TasksMax': '32', 'Type': 'oneshot', 'TimeoutStartSec': '120',
                 'NoNewPrivileges': 'true', 'PrivateTmp': 'true', 'ProtectSystem': 'strict',
                 'ProtectHome': 'true', 'UMask': '0077',
                 'ReadWritePaths': str(READ_PARENT if reader else BASE)}
        command = ['systemd-run', '--wait', '--pipe', '--unit='+unit]
        command += ['--property='+k+'='+v for k, v in props.items()]
        command += ['/opt/greenmind/archive-python-20261008-v4/bin/python',
                    str(PHASE),
                    '--phase', name, *arguments]
        result = subprocess.run(command, capture_output=True, timeout=135)
        # Retain sanitized worker output and the exact bounded unit identity.
        write_new(CONTROL/(unit+'.json'), json.dumps({'unit': unit,
                  'exit': result.returncode, 'stdout': result.stdout.decode()[-8192:]}).encode())
        if result.returncode == 0:
            return
        lines = result.stdout.decode().splitlines()
        last = json.loads(lines[-1]) if lines else {}
        if last.get('status') != 'paused' or attempt == 3:
            raise RuntimeError('Bounded phase did not complete: '+name)
        time.sleep(20)


def main():
    assert os.geteuid() == 0 and os.uname().nodename == 'ubuntu-4gb-fsn1-1'
    for name, sha in json.loads((RUNTIME/'files.json').read_text()).items():
        assert hashlib.sha256((RUNTIME/name).read_bytes()).hexdigest() == sha
    assert not CONTROL.exists() and PUBLISH_ENV.exists() and OUTPUT.exists()
    assert not READ_OUTPUT.exists() and not REFERENCE.exists()
    for p in (LEDGER, GATEWAY, DIRECT):
        assert (p/'stage.json').is_file()
    d = dict(x.split('=',1) for x in Path('/etc/greenmind/archive-offload-v10/access.env').read_text().splitlines() if x and not x.startswith('#'))
    d.update(PYTHONPATH=str(RUNTIME/'backend'), PYTHONDONTWRITEBYTECODE='1',
             RAW_ARCHIVE_ENABLED='false', RAW_ARCHIVE_DELETE_ENABLED='false',
             RAW_ARCHIVE_LEGACY_NULL_ACCEPTED='false', RETENTION_ENABLED='false',
             DIRECT_RETENTION_ENABLED='false', VISUAL_PRUNE_ENABLED='false')
    assert d['RAW_ARCHIVE_SOURCE_MIN_MEMORY_MIB']=='640'
    assert d['RAW_ARCHIVE_MIN_AVAILABLE_MEMORY_MIB']=='800'
    assert d['RAW_ARCHIVE_SOURCE_MAX_LOAD']=='2'
    assert PUBLISH_ENV.read_bytes() == ''.join(k+'='+v+'\n' for k,v in sorted(d.items())).encode()
    CONTROL.mkdir(mode=0o700)
    manifest = OUTPUT/'manifest.json'
    assert hashlib.sha256(manifest.read_bytes()).hexdigest() == PREPARED_SHA
    count = len(json.loads(manifest.read_text())['files'])
    for position in range(count):
        phase('publish-part', ['--output',str(OUTPUT), '--manifest',str(manifest), '--position',str(position)])
    phase('publish-manifest', ['--output',str(OUTPUT), '--manifest',str(manifest)])
    write_new(REFERENCE, (OUTPUT/'publication.json').read_bytes(), 995, 982)
    phase('read-manifest', ['--output',str(READ_OUTPUT), '--reference',str(REFERENCE)], True)
    for position in range(count):
        phase('restore-part', ['--output',str(READ_OUTPUT), '--position',str(position)], True)
    phase('finish', ['--output',str(READ_OUTPUT), '--reference',str(REFERENCE)], True)
    proof = json.loads((READ_OUTPUT/'verified.json').read_text())
    assert proof['complete'] and proof['independent_read_identity']
    write_new(CONTROL/'complete.json', json.dumps({'complete':True, 'parts':count,
               'reference':json.loads(REFERENCE.read_text()), 'verified':str(READ_OUTPUT/'verified.json'),
               'deleted_files':0, 'deletion_enabled':False}).encode())
    print(json.dumps({'status':'complete_independent_catalog_recovery','parts':count,'deleted_files':0}))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'status':'blocked','error_type':type(error).__name__, 'deleted_files':0}))
        raise SystemExit(1) from None
