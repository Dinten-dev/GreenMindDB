"""Finite credential-free offline validation, reusing accepted small tables."""
import hashlib
import json
import os
import pwd
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

RUNTIME = Path('/opt/greenmind/archive-preparation/20261010-v41')
PYTHON = '/opt/greenmind/archive-python-20261008-v4/bin/python'
BASE = Path('/var/lib/greenmind/archive/catalog-preparation-20261009/ranges1024-20261010T184450')
SOURCE = BASE/'capture'
OUTPUT = BASE/'chunk-offline'
STAMP = datetime.now(UTC).strftime('%Y%m%dt%H%M%S')
ACCOUNT = 'greenmind-archive-copy'

def phase(label, arguments, proof_path, account):
    unit = 'greenmind-chunk-' + label.replace('_', '-') + '-' + STAMP
    subprocess.run(['systemd-run', '--no-block', '--unit=' + unit,
        '--property=Type=oneshot', '--property=User=' + ACCOUNT,
        '--property=Group=' + ACCOUNT, '--property=Slice=greenmind-archive.slice',
        '--property=Environment=PYTHONPATH=' + str(RUNTIME/'backend'),
        '--property=MemoryMax=384M', '--property=MemorySwapMax=0',
        '--property=CPUQuota=50%', '--property=TimeoutStartSec=125',
        '--property=Nice=19', '--property=IOSchedulingClass=idle',
        '--property=ProtectSystem=strict', '--property=NoNewPrivileges=true',
        '--property=PrivateTmp=true', '--property=ReadWritePaths=' + str(OUTPUT),
        '--property=UMask=0077', PYTHON, str(RUNTIME/'deploy/archive-server/catalog-chunk-phase.py'),
        '--source', str(SOURCE), *arguments], check=True, timeout=15)
    deadline = time.monotonic()+132
    while time.monotonic() < deadline:
        status = subprocess.check_output(['systemctl', 'show', unit, '--value',
                                          '-p', 'ActiveState'], text=True, timeout=5).strip()
        if status in {'inactive', 'failed'}:
            assert status == 'inactive' and proof_path.is_file(), 'Bounded chunk not accepted'
            assert proof_path.stat().st_uid == account.pw_uid
            assert json.loads(proof_path.read_text()).get('complete') is True
            return
        time.sleep(2)
    raise RuntimeError('Bounded phase did not finish')

def main():
    assert os.geteuid() == 0 and os.uname().nodename == 'ubuntu-4gb-fsn1-1'
    manifest = json.loads((RUNTIME/'files.json').read_text())
    assert all(hashlib.sha256((RUNTIME/name).read_bytes()).hexdigest() == value for name,value in manifest.items())
    capture_sha = hashlib.sha256((SOURCE/'capture.json').read_bytes()).hexdigest()
    capture = json.loads((SOURCE/'capture.json').read_text())
    assert capture['complete'] is True and capture['kind'] == 'gateway'
    account = pwd.getpwnam(ACCOUNT)
    OUTPUT.mkdir(mode=0o700, exist_ok=True)
    assert not OUTPUT.is_symlink() and OUTPUT.stat().st_uid == account.pw_uid
    os.chown(OUTPUT, account.pw_uid, account.pw_gid)
    tables = {}
    for entry in capture['files']:
        table = entry['table']
        assert table.replace('_','').isalnum()
        old = OUTPUT/table/'table'
        if not (old/'table.json').exists():
            old = BASE/'offline'/table
        if (old/'table.json').exists():
            proof = json.loads((old/'table.json').read_text())
            assert proof['complete'] is True and proof['capture_sha256'] == capture_sha and proof['rows'] == entry['rows']
            tables[table] = str(old)
            continue
        root = OUTPUT/table
        root.mkdir(mode=0o700, exist_ok=True)
        assert not root.is_symlink()
        os.chown(root, account.pw_uid, account.pw_gid)
        chunks = root/'raw'
        if not (chunks/'chunks.json').exists():
            assert not chunks.exists(), 'Retain incomplete raw framing; new explicit destination required'
            phase(table+'-prepare', ['--phase','prepare','--table',table,'--output',str(chunks)], chunks/'chunks.json', account)
        metadata = json.loads((chunks/'chunks.json').read_text())
        phases = []
        for position in range(len(metadata['chunks'])):
            folder = root/f'validated-{position:06d}'
            if not (folder/'validated.json').exists():
                if folder.exists():
                    folder = root/f'validated-{position:06d}-{STAMP}'
                phase(table+f'-{position:06d}', ['--phase','process','--chunks',str(chunks),
                    '--position',str(position),'--output',str(folder)], folder/'validated.json', account)
            phases.append(str(folder))
        phase_list = root/('phases-' + STAMP + '.json')
        phase_list.write_text(json.dumps(phases)); phase_list.chmod(0o600)
        os.chown(phase_list, account.pw_uid, account.pw_gid)
        folder = root/'table'
        phase(table+'-assemble', ['--phase','assemble','--chunks',str(chunks),
            '--phases',str(phase_list),'--output',str(folder)], folder/'table.json', account)
        tables[table] = str(folder)
    result = {'schema': 1, 'complete': True, 'capture_sha256': capture_sha,
              'tables': tables, 'source_sql_opened': False, 'deleted_files': 0}
    with (OUTPUT/'validated-tables.json').open('x') as stream:
        os.fchmod(stream.fileno(),0o600)
        os.fchown(stream.fileno(),account.pw_uid,account.pw_gid)
        json.dump(result,stream)
    print(json.dumps(result))

if __name__ == '__main__':
    main()
