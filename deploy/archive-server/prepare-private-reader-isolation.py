"""Separate private reader scratch areas and hide broker/worker keys from API."""
import json
import os
import pwd
import subprocess
from pathlib import Path

ROOT=Path('/etc/greenmind/archive-reader-20261010')
STATE=Path('/var/lib/greenmind/archive-reader-20261010')
RUNTIME=Path('/opt/greenmind/archive-preparation/20261010-v48')

def env(path):
    return dict(l.split('=',1) for l in path.read_text().splitlines() if l and not l.startswith('#'))

def main():
    assert os.geteuid()==0 and os.uname().nodename=='ubuntu-4gb-fsn1-1'
    a=pwd.getpwnam('greenmind-archive-read')
    evidence=RUNTIME/'reader-isolation-originals';evidence.mkdir(mode=0o700)
    for name in ('api','broker','export'):
        p=STATE/'scratch'/name;p.mkdir(mode=0o700);os.chown(p,a.pw_uid,a.pw_gid)
    for kind in ('api','broker'):
        file=ROOT/('read.env' if kind=='api' else 'broker.env');old=file.read_bytes()
        saved=evidence/file.name;saved.write_bytes(old);saved.chmod(0o600)
        d=env(file);d['RAW_ARCHIVE_READ_SCRATCH_DIR']=str(STATE/'scratch'/kind)
        file.write_text(''.join(k+'='+v+'\n' for k,v in sorted(d.items())))
        file.chmod(0o640);os.chown(file,0,a.pw_gid)
        unit='greenmind-archive-reader-'+kind+'-20261010.service'
        drop=Path('/etc/systemd/system')/(unit+'.d');drop.mkdir(exist_ok=True)
        target=drop/'private-credential-isolation.conf'
        assert not target.exists()
        blocked=['/etc/greenmind/archive-copy-20261010','/etc/greenmind/archive-offload-v10',
                 '/etc/greenmind/archive-source-read-v12','/etc/greenmind/archive-diagnostic-read-20261010',
                 '-/etc/greenmind/archive-export-20261010']
        # Replaced by a mandatory mask once the export directory is provisioned.
        # That transition must recreate the private broker's mount namespace.
        writable=str(STATE/'scratch'/kind)
        if kind=='api':
            blocked += [str(ROOT),'/etc/greenmind/archive-reader-validation-v10']
            writable+=' '+str(STATE/'jobs')
        else:
            writable+=' /run/greenmind-archive-reader-20261010'
        target.write_text('[Service]\nReadWritePaths=\nReadWritePaths='+writable+'\nInaccessiblePaths='+' '.join(blocked)+'\n')
        target.chmod(0o644)
        subprocess.run(['systemd-analyze','verify',unit],check=True,capture_output=True,timeout=10)
    subprocess.run(['systemctl','daemon-reload'],check=True,timeout=15)
    proof={'prepared':True,'restarted':False,'api_upload_access':False,
           'scratch_separated':True,'public_routes_changed':False,'deleted_files':0}
    (evidence/'prepared.json').write_text(json.dumps(proof));(evidence/'prepared.json').chmod(0o600)
    print(json.dumps(proof))

if __name__=='__main__':main()
