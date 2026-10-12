"""Preserve the restricted SSH authorization; improve bounded pause diagnostics."""
import hashlib
import json
import os
import pwd
import subprocess
import tarfile
from pathlib import Path

ROOT=Path('/opt/greenmind/archive-preparation/20261010-v48')
OLD=b'PYTHONPATH=/opt/greenmind/archive-preparation/20261010-health-v9/backend'
SHA='28176086df7e9b52af279895feec95bff2199afe7367fdc66cfa1521b57b08e7'

def protected():
    names=['gm-zones-production-2e687bec010a-application-1','gm-peter-receiver-20261010',
           'gm-direct-production-direct-api-1','greenminddb-backend-1']
    data=json.loads(subprocess.check_output(['docker','inspect',*names],timeout=10))
    return [(r['Name'],r['Image'],r['State']['Running'],r['State']['StartedAt'],r['RestartCount']) for r in data]

def main():
    assert os.geteuid()==0 and os.uname().nodename=='Biolingo'
    memory=int(next(l.split()[1] for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:')))/1024
    assert memory>=688 and os.getloadavg()[0]<=2.4
    source=Path('/home/traver/checkpoint-preparation-20261010-v48.tar.gz')
    assert hashlib.sha256(source.read_bytes()).hexdigest()==SHA and not ROOT.exists()
    baseline=protected(); assert all(r[2] for r in baseline)
    proxy=Path('/etc/nginx/sites-available/greenmind-prod').read_bytes()
    with tarfile.open(source) as archive:
        items=archive.getmembers();assert all(m.isfile() and not Path(m.name).is_absolute() and '..'not in Path(m.name).parts for m in items)
        assert len({m.name for m in items})==len(items)
        hashes=json.load(archive.extractfile('files.json'))
        assert set(hashes)|{'files.json'}=={m.name for m in items}
        ROOT.mkdir(mode=0o755)
        for m in items:
            raw=archive.extractfile(m).read()
            assert m.name=='files.json' or hashlib.sha256(raw).hexdigest()==hashes[m.name]
            p=ROOT/m.name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw);p.chmod(0o644)
    a=pwd.getpwnam('greenmind-archive-lease');target=Path(a.pw_dir)/'.ssh/authorized_keys'
    raw=target.read_bytes();assert raw.count(OLD)==1 and raw.count(b'command=')==1
    assert b'from="2.31.22.33",restrict,' in raw and b' -m app.raw_archive.remote_lease --serve' in raw
    private=ROOT/'operator-evidence';private.mkdir(mode=0o700)
    (private/'authorization-before').write_bytes(raw);(private/'authorization-before').chmod(0o600)
    candidate=raw.replace(OLD,('PYTHONPATH='+str(ROOT/'backend')).encode())
    pending=target.parent/'authorization-v48.pending'
    with pending.open('xb') as out:
        os.fchmod(out.fileno(),0o600);os.fchown(out.fileno(),a.pw_uid,a.pw_gid)
        out.write(candidate);out.flush();os.fsync(out.fileno())
    assert target.read_bytes()==raw and protected()==baseline
    assert Path('/etc/nginx/sites-available/greenmind-prod').read_bytes()==proxy
    os.replace(pending,target)
    proof={'status':'restricted_runtime_updated','source_lease_maximum_seconds':120,
           'source_memory_reserve_mib':640,'source_max_load':2.4,
           'authorization_sha256':hashlib.sha256(candidate).hexdigest(),
           'receivers_unchanged':True,'new_admin_rights':False,'deleted_files':0}
    (private/'verified.json').write_text(json.dumps(proof));(private/'verified.json').chmod(0o600)
    print(json.dumps(proof))

if __name__=='__main__':main()
