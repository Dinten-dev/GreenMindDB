"""Finite offline coordinator. SQL and upload credentials are never supplied."""
import hashlib
import json
import os
import pwd
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

RUNTIME = Path('/opt/greenmind/archive-preparation/20261010-v39')
PYTHON = '/opt/greenmind/archive-python-20261008-v4/bin/python'
SOURCE = Path('/var/lib/greenmind/archive/catalog-preparation-20261009/ranges1024-20261010T184450/capture')
OUTPUT = SOURCE.parent / 'offline'
ACCOUNT = 'greenmind-archive-copy'

def main():
    assert os.geteuid() == 0 and os.uname().nodename == 'ubuntu-4gb-fsn1-1'
    assert not SOURCE.is_symlink() and (SOURCE / 'capture.json').is_file()
    manifest = json.loads((RUNTIME / 'files.json').read_text())
    assert all(hashlib.sha256((RUNTIME / name).read_bytes()).hexdigest() == digest
               for name, digest in manifest.items())
    proof = json.loads((SOURCE / 'capture.json').read_text())
    assert proof['kind'] == 'gateway' and len(proof['files']) == 11
    account = pwd.getpwnam(ACCOUNT)
    OUTPUT.mkdir(mode=0o700)
    os.chown(OUTPUT, account.pw_uid, account.pw_gid)
    completed = {}
    for entry in proof['files']:
        table = entry['table']
        assert table in {'organization', 'zone', 'gateway', 'sensor', 'wav_file',
            'wav_feature', 'wav_feature_version', 'visual_reading', 'visual_wav',
            'visual_wave', 'visual_chunk'}
        unit = 'greenmind-offline-' + table.replace('_', '-') + '-20261010t1848'
        command = ['systemd-run', '--no-block', '--unit=' + unit,
            '--property=Type=oneshot', '--property=User=' + ACCOUNT,
            '--property=Group=' + ACCOUNT, '--property=Slice=greenmind-archive.slice',
            '--property=Environment=PYTHONPATH=' + str(RUNTIME / 'backend'),
            '--property=MemoryMax=384M', '--property=MemorySwapMax=0',
            '--property=CPUQuota=50%', '--property=TimeoutStartSec=125',
            '--property=Nice=19', '--property=IOSchedulingClass=idle',
            '--property=UMask=0077', PYTHON, '-m', 'app.raw_archive.catalog_offline',
            '--source', str(SOURCE), '--table', table, '--output', str(OUTPUT / table)]
        subprocess.run(command, check=True, timeout=15)
        deadline = time.monotonic() + 130
        while time.monotonic() < deadline:
            meta = OUTPUT / table / 'table.json'
            status = subprocess.check_output(['systemctl', 'show', unit,
                '--value', '-p', 'ActiveState'], text=True, timeout=5).strip()
            if status in {'inactive', 'failed'}:
                assert status == 'inactive' and meta.is_file(), 'Offline phase not accepted'
                part = json.loads(meta.read_text())
                assert part['complete'] is True and part['rows'] == entry['rows']
                completed[table] = {'rows': part['rows'], 'parts': len(part['files'])}
                break
            time.sleep(2)
        else:
            raise RuntimeError('Bounded offline unit did not finish')
    result = {'at': datetime.now(UTC).isoformat(), 'tables': completed,
              'source_sql_opened': False, 'deleted_files': 0}
    with (OUTPUT / 'validated.json').open('x') as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(result, stream)
    print(json.dumps(result))

if __name__ == '__main__':
    main()
