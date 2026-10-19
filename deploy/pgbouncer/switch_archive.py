"""Private candidate and reversible pool overlay; no copy ownership changes."""
import argparse
import hashlib
import json
import os
import pwd
import secrets
import subprocess
import time
from pathlib import Path
from urllib.request import Request, urlopen

from sqlalchemy.engine import make_url

ROOT=Path('/etc/greenmind/pgbouncer-20261011')
JOBS=[('greenmind-archive-reader-api-20261010.service','/etc/greenmind/archive-reader-20261010/read.env','reader.env'),('greenmind-archive-export-20261010.service','/etc/greenmind/archive-export-20261010/export.env','export.env'),('greenmind-archive-copy.service','/etc/greenmind/archive-copy-20261010/copy.env','copy.env')]


def private(path,raw):
    with path.open('xb')as out:os.fchmod(out.fileno(),0o600);out.write(raw)


def guard(require_pool=True):
    available=int(next(x.split()[1]for x in Path('/proc/meminfo').read_text().splitlines()if x.startswith('MemAvailable:')))/1024
    assert available>=864 and os.getloadavg()[0]<=2
    for port in (18120,18003,18000):
        try:
            with urlopen(f'http://127.0.0.1:{port}/health',timeout=3)as r:assert r.status==200
        except Exception as error:
            raise RuntimeError('source_health_port_'+str(port)+'_'+type(error).__name__)from None
    if require_pool:
        assert subprocess.run(['systemctl','is-active','--quiet','greenmind-pgbouncer-20261011.service']).returncode==0
        settings=dict(l.split('=',1)for l in Path('/etc/greenmind/archive-reader-20261010/broker.env').read_text().splitlines()if l and not l.startswith('#'))
        nonce=secrets.token_hex(16);began=time.monotonic()
        request=Request(settings['RAW_ARCHIVE_SOURCE_PROBE_URL'],headers={
            'Authorization':'Bearer '+Path(settings['RAW_ARCHIVE_SOURCE_PROBE_TOKEN_FILE']).read_text().strip(),
            'X-GreenMind-Probe-Nonce':nonce})
        with urlopen(request,timeout=3)as response:source=json.loads(response.read(8193))
        assert source['probe_nonce']==nonce and source['sample_age_seconds']+time.monotonic()-began<=10
        assert source['available_mib']>=640 and source['load']<=2.4
        assert source['routes']=={'8120':200,'8003':200,'8000':200}
    return available


def prepare():
    guard();assert not(ROOT/'overlays.json').exists();rows=[]
    for unit,source,name in JOBS:
        p=Path(source);values=dict(l.split('=',1)for l in p.read_text().splitlines()if l and not l.startswith('#'))
        assert values['RAW_ARCHIVE_DELETE_ENABLED']=='false'
        pooled={k:make_url(v).set(port=6432).render_as_string(hide_password=False)for k,v in values.items()if k.endswith('DATABASE_URL')}
        assert len(pooled)==2
        pooled['GREENMIND_TRANSACTION_POOL_READS']='true'
        pooled['PYTHONPATH']='/opt/greenmind/archive-preparation/20261011-pgbouncer-v69/backend'
        if name!='copy.env':pooled['RELEASE_ID']='archive-reader-20261011-pgbouncer-v69'
        target=ROOT/name;private(target,''.join(k+'='+v+'\n'for k,v in pooled.items()).encode())
        override=Path('/etc/systemd/system')/(unit+'.d')/'70-pgbouncer-20261011.conf'
        assert not override.exists()
        new='[Unit]\nAfter=greenmind-pgbouncer-20261011.service\nRequires=greenmind-pgbouncer-20261011.service\n[Service]\nEnvironmentFile='+str(target)+'\n'
        rows.append({'unit':unit,'source':source,'source_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'env':str(target),'env_sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'override':str(override),'content':new,'override_sha256':hashlib.sha256(new.encode()).hexdigest()})
    private(ROOT/'overlays.json',json.dumps({'rows':rows,'source_link_unchanged':True,'writer_ownership_unchanged':True,'deleted_files':0}).encode())
    unit=Path('/etc/systemd/system/greenmind-pgbouncer-reader-candidate-v68.service')
    assert not unit.exists()
    candidate=subprocess.check_output(['systemctl','cat',JOBS[0][0]],text=True,timeout=5)
    candidate=candidate.replace('--port 8141','--port 8142')
    assert '--port 8142'in candidate and '--port 8141'not in candidate
    # Add overrides last; preserve all existing namespace/credential restrictions.
    candidate+='\n'+rows[0]['content']
    unit.write_text(candidate);unit.chmod(0o644)
    subprocess.run(['systemd-analyze','verify',str(unit)],check=True,capture_output=True,timeout=10)
    subprocess.run(['systemctl','daemon-reload'],check=True,capture_output=True,timeout=10)
    print(json.dumps({'prepared_inactive':True,'candidate_port':8142,'planned_backends_maximum':4,'writer_ownership_unchanged':True,'original_environments_preserved':True,'deleted_files':0}))


def apply(rollback=False):
    minimum=guard(require_pool=not rollback);manifest=json.loads((ROOT/'overlays.json').read_text())
    for row in manifest['rows']:
        assert hashlib.sha256(Path(row['source']).read_bytes()).hexdigest()==row['source_sha256']
        assert hashlib.sha256(Path(row['env']).read_bytes()).hexdigest()==row['env_sha256']
        p=Path(row['override']);p.parent.mkdir(mode=0o755,exist_ok=True)
        body='[Service]\nEnvironmentFile='+row['source']+'\n'if rollback else row['content']
        if p.exists():
            current=p.read_text();assert current==row['content']or current=='[Service]\nEnvironmentFile='+row['source']+'\n'
        p.write_text(body);p.chmod(0o644)
    subprocess.run(['systemctl','daemon-reload'],check=True,capture_output=True,timeout=10)
    # No timer start/stop, no receiver changes; one read API briefly restarts.
    subprocess.run(['systemctl','restart',JOBS[0][0]],check=True,capture_output=True,timeout=15)
    # Imports run within the unchanged 25% CPU budget. Bound the cold-start
    # allowance without weakening per-request or source resource guards.
    deadline=time.monotonic()+60
    try:
        while True:
            try:
                with urlopen('http://127.0.0.1:8141/health',timeout=2)as r:
                    assert r.status==200
                    data=json.loads(r.read(8192));break
            except Exception:
                if time.monotonic()>deadline:raise RuntimeError('Reader health deadline')
                time.sleep(.5)
        minimum=min(minimum,guard(require_pool=not rollback))
    except Exception:
        if not rollback:apply(True)
        raise
    result={'at':time.time(),'pooled':not rollback,'actual_rollback':rollback,'reader_health':200,'minimum_available_mib':minimum,'copy_and_export_schedules_unchanged':True,'source_link_unchanged':True,'deleted_files':0}
    private(ROOT/('overlay-'+str(time.time_ns())+'.json'),json.dumps(result).encode());print(json.dumps(result))


if __name__=='__main__':
    assert os.geteuid()==0 and os.uname().nodename=='ubuntu-4gb-fsn1-1'
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['prepare','activate','rollback']);action=parser.parse_args().action
    if action=='prepare':prepare()
    else:apply(action=='rollback')
