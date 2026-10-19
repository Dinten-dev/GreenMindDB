"""Install immutable private archive fixes; no timers or receivers are changed."""
import hashlib
import json
import os
import tarfile
from pathlib import Path

ROOT = Path('/opt/greenmind/archive-preparation/20261010-v46')
SHA = '329bda9ba4b3827a935a932abfc9ee9e74b97082c2a28832beae15a2d5eaa763'

def main():
    assert os.geteuid()==0 and os.uname().nodename=='ubuntu-4gb-fsn1-1'
    source=Path('/home/traver/archive-v46.tar.gz')
    assert hashlib.sha256(source.read_bytes()).hexdigest()==SHA and not ROOT.exists()
    with tarfile.open(source) as archive:
        items=archive.getmembers()
        assert all(m.isfile() and not Path(m.name).is_absolute() and '..' not in Path(m.name).parts for m in items)
        assert len({m.name for m in items})==len(items)
        hashes=json.load(archive.extractfile('files.json'))
        assert set(hashes)|{'files.json'}=={m.name for m in items}
        ROOT.mkdir(mode=0o755)
        for member in items:
            raw=archive.extractfile(member).read()
            assert member.name=='files.json' or hashlib.sha256(raw).hexdigest()==hashes[member.name]
            path=ROOT/member.name;path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(raw);path.chmod(0o644)
    backups=ROOT/'profile-originals';backups.mkdir(mode=0o700)
    for profile in [Path('/etc/greenmind/archive-copy-20261010/copy.env'),
                    Path('/etc/greenmind/archive-catalog-publish-20261010.env'),
                    Path('/etc/greenmind/archive-catalog-read-20261010.env'),
                    Path('/etc/greenmind/archive-reader-20261010/read.env'),
                    Path('/etc/greenmind/archive-reader-20261010/broker.env')]:
        raw=profile.read_bytes();stat=profile.stat()
        saved=backups/(profile.parent.name+'-'+profile.name)
        saved.write_bytes(raw);saved.chmod(0o600)
        lines=raw.decode().splitlines()
        assert sum(l.startswith('PYTHONPATH=') for l in lines)==1
        values=[('PYTHONPATH='+str(ROOT/'backend') if l.startswith('PYTHONPATH=') else l) for l in lines]
        if profile.name=='read.env':
            values=[('RELEASE_ID=archive-reader-20261010-v46' if l.startswith('RELEASE_ID=') else l) for l in values]
        profile.write_text('\n'.join(values)+'\n')
        os.chown(profile,stat.st_uid,stat.st_gid);profile.chmod(stat.st_mode & 0o777)
    print(json.dumps({'installed':str(ROOT),'files':len(hashes),'services_changed':False,'deleted_files':0}))

if __name__=='__main__':
    main()
