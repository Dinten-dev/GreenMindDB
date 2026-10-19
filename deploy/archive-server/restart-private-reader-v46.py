"""Guarded private reader upgrade; no public routes or schedules."""
import hashlib
import json
import os
import secrets
import subprocess
import time
from pathlib import Path
from urllib.request import Request, urlopen

ROOT=Path('/etc/greenmind/archive-reader-20261010')
UNITS=['greenmind-archive-reader-broker-20261010.service',
       'greenmind-archive-reader-api-20261010.service']

def main():
    assert os.geteuid()==0 and os.uname().nodename=='ubuntu-4gb-fsn1-1'
    memory=int(next(l.split()[1] for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:')))/1024
    assert memory>=1088 and os.getloadavg()[0]<=2
    profile=dict(l.split('=',1) for l in (ROOT/'broker.env').read_text().splitlines() if l and not l.startswith('#'))
    nonce=secrets.token_hex(16)
    began=time.monotonic()
    request=Request(profile['RAW_ARCHIVE_SOURCE_PROBE_URL'],headers={
        'Authorization':'Bearer '+Path(profile['RAW_ARCHIVE_SOURCE_PROBE_TOKEN_FILE']).read_text().strip(),
        'X-GreenMind-Probe-Nonce':nonce})
    with urlopen(request,timeout=3) as response:
        source=json.loads(response.read(8193))
    assert source['probe_nonce']==nonce and source['sample_age_seconds']+time.monotonic()-began<=10
    assert source['available_mib']>=640 and source['load']<=2
    assert source['routes']=={'8120':200,'8003':200,'8000':200}
    runtime=Path('/opt/greenmind/archive-preparation/20261010-v46')
    for name,digest in json.loads((runtime/'files.json').read_text()).items():
        assert hashlib.sha256((runtime/name).read_bytes()).hexdigest()==digest
    read=dict(l.split('=',1) for l in (ROOT/'read.env').read_text().splitlines() if l and not l.startswith('#'))
    assert read['RELEASE_ID']=='archive-reader-20261010-v46'
    assert not any(k.startswith('RAW_ARCHIVE_SFTP_') for k in read)
    for unit in UNITS:
        subprocess.run(['systemctl','restart',unit],check=True,timeout=25)
    deadline=time.monotonic()+25
    while True:
        try:
            with urlopen('http://127.0.0.1:8141/health',timeout=3) as response:
                health=json.loads(response.read(8193))
            assert health['release_id']=='archive-reader-20261010-v46'
            break
        except Exception:
            if time.monotonic()>deadline:
                raise
            time.sleep(1)
    proof={'status':'running_private','release_id':health['release_id'],
           'public_routes_changed':False,'accepted':False,'deleted_files':0}
    target=runtime/'reader-started.json';target.write_text(json.dumps(proof));target.chmod(0o600)
    print(json.dumps(proof))

if __name__=='__main__':main()
