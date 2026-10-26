"""Consistent private export-job checkpoint, retaining all original files."""
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

SOURCE=Path('/mnt/HC_Volume_106755700/greenmind-archive-exports/33fe395')
BASE=Path('/mnt/HC_Volume_106755700/greenmind-feature-backup')
MARKER=Path('/etc/greenmind/archive-export-owner.json')

def main():
    assert os.geteuid()==0 and os.uname().nodename=='Biolingo'
    memory=int(next(l.split()[1] for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:')))/1024
    if memory<688 or os.getloadavg()[0]>2.4:raise SystemExit(75)
    sys.path.insert(0,'/opt/greenmind/archive-preparation/20261010-v48/backend')
    from app.raw_archive.observation import protected
    from app.raw_archive.source_probe import gateway_ports
    from urllib.request import urlopen
    for port in (*gateway_ports(),8003,8000):
        try:
            with urlopen('http://127.0.0.1:'+str(port)+'/health',timeout=2)as response:
                if response.status!=200:raise SystemExit(75)
        except OSError:raise SystemExit(75)from None
    receivers=protected()
    assert SOURCE.is_dir() and not SOURCE.is_symlink()
    original=Path('/etc/nginx/sites-available/greenmind-prod').read_bytes()
    if not MARKER.exists():
        MARKER.write_text(json.dumps({'schema':1,'owner':'handoff_pending','accepted':False,'deleted_files':0}))
        MARKER.chmod(0o644)
        drop=Path('/etc/systemd/system/greenmind-archive-export.service.d')
        drop.mkdir(exist_ok=True)
        p=drop/'zzzz-archive-owner-fence.conf'
        with p.open('x') as out:out.write('[Unit]\nConditionPathExists=!'+str(MARKER)+'\n')
        p.chmod(0o644)
        subprocess.run(['systemctl','daemon-reload'],check=True,timeout=15)
    subprocess.run(['systemctl','stop','greenmind-archive-export.timer','greenmind-archive-export.service'],check=True,timeout=25)
    for u in ('greenmind-archive-export.timer','greenmind-archive-export.service'):
        assert subprocess.check_output(['systemctl','show',u,'-p','ActiveState','--value'],timeout=5).strip() in (b'inactive',b'failed')
    stamp=datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f')
    target=BASE/('export-jobs-'+stamp);target.mkdir(mode=0o700)
    with sqlite3.connect('file:'+str(SOURCE/'jobs.sqlite3')+'?mode=ro',uri=True,timeout=3) as source,sqlite3.connect(target/'jobs.sqlite3') as restored:
        source.backup(restored)
        assert restored.execute('PRAGMA integrity_check').fetchone()==('ok',)
        counts=restored.execute('SELECT count(*) FROM jobs').fetchone()[0]
        names=set(n for row in restored.execute('SELECT parts FROM jobs') for n in json.loads(row[0]))
        assert all(Path(n).name==n and n.endswith('.zip') for n in names)
    files=[p for p in SOURCE.iterdir() if p.name!='jobs.sqlite3' and not p.name.startswith('jobs.sqlite3-')]
    assert sum(p.stat().st_size for p in files)<=32*1024**2
    assert names<={p.name for p in files}
    for p in files:
        assert p.is_file() and not p.is_symlink() and p.name.endswith(('.zip','.part','.raw-part'))
        shutil.copyfile(p,target/p.name)
    manifest={p.name:{'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in target.iterdir()}
    for p in target.iterdir():p.chmod(0o600)
    proof={'schema':1,'complete':True,'created_at':datetime.now(UTC).isoformat(),
           'jobs':counts,'files':manifest,'source':str(SOURCE),'originals_retained':True,'deleted_files':0}
    path=target/'manifest.json';path.write_text(json.dumps(proof,sort_keys=True));path.chmod(0o600)
    assert Path('/etc/nginx/sites-available/greenmind-prod').read_bytes()==original
    assert protected()==receivers
    subprocess.run(['setfacl','-m','u:traver:--x',str(target)],check=True,timeout=5)
    for p in target.iterdir():subprocess.run(['setfacl','-m','u:traver:r--',str(p)],check=True,timeout=5)
    print(json.dumps({'status':'consistent_export_checkpoint','path':str(target),'jobs':counts,
                      'files':len(manifest),'manifest_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'deleted_files':0}))

if __name__=='__main__':main()
