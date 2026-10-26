"""Hash-pinned pooling of unrouted admin fallbacks, with retained rollback."""
import argparse
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from urllib.request import urlopen

from sqlalchemy.engine import make_url

ROOT = Path('/etc/greenmind/pgbouncer-fallback-20261011')
PROTECTED = ['gm-zones-production-2e687bec010a-application-1', 'gm-peter-receiver-20261010', 'gm-direct-production-direct-api-1', 'greenminddb-backend-1', 'gm-admin-production-92e29a3-application-1', 'gm-archive-production-33fe395-api-1', 'gm-peter-frontend-v3-20261010', 'gm-english-production-6fe1b5f']
PROXY = Path('/etc/nginx/sites-available/greenmind-prod')


def private(path, raw):
    with path.open('xb') as out: os.fchmod(out.fileno(),0o600); out.write(raw)


def markers():
    return {name: json.loads(subprocess.check_output(['docker','inspect','--format', '{"image":{{json .Image}},"started":{{json .State.StartedAt}},"restarts":{{.RestartCount}},"running":{{.State.Running}}}',name], timeout=5)) for name in PROTECTED}


def guard():
    available = int(next(x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:'))) / 1024
    assert available >= 736 and os.getloadavg()[0] <= 2.4
    for port in (8120,8003,8000):
        with urlopen(f'http://127.0.0.1:{port}/health',timeout=3) as response: assert response.status == 200
    return available


def prepare():
    guard(); before=markers(); raw=PROXY.read_bytes()
    # Neither fallback API port is in the live proxy; all static routes retained.
    assert b'127.0.0.1:8130' not in raw and b'127.0.0.1:8131' not in raw
    assert not (ROOT/'fallbacks.json').exists()
    rows=[]
    for revision,port in [('1b449e9',8130),('e148f83',8131)]:
        base=Path('/home/traver/greenmind-admin-production')/revision
        source=base/'application.env'; compose=base/'compose.json'
        values=dict(l.split('=',1) for l in source.read_text().splitlines() if l and not l.startswith('#'))
        assert values.get('RETENTION_ENABLED','false')=='false', 'Retention remains disabled'
        assert values.get('EMBEDDED_BACKGROUND_WORKERS_ENABLED','false')=='false', 'Fallback must not start background writers'
        url=make_url(values['DATABASE_URL']); assert url.username=='admin' and url.database=='plantdb'
        values['DATABASE_URL']=url.set(host='172.28.20.1',port=6432).render_as_string(hide_password=False)
        name='gm-admin-production-'+revision+'-application-1'
        info=json.loads(subprocess.check_output(['docker','inspect','--format','{"image":{{json .Image}},"networks":{{json .NetworkSettings.Networks}}}',name],timeout=5))
        target=base/'pgbouncer-20261011.env'
        private(target, ''.join(k+'='+v+'\n' for k,v in values.items()).encode())
        override=base/'pgbouncer-20261011.json'
        private(override,json.dumps({'services':{'application':{'image':info['image'],'env_file':[str(target)]}}}).encode())
        restore=base/'pgbouncer-20261011.rollback.json'
        private(restore,json.dumps({'services':{'application':{'image':info['image']}}}).encode())
        rows.append({'revision':revision,'name':name,'port':port,'compose':str(compose),'compose_sha256':hashlib.sha256(compose.read_bytes()).hexdigest(),'source_env_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'env':str(target),'env_sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'override':str(override),'override_sha256':hashlib.sha256(override.read_bytes()).hexdigest(),'rollback':str(restore),'rollback_sha256':hashlib.sha256(restore.read_bytes()).hexdigest(),'image':info['image']})
    proof={'rows':rows,'protected':before,'nginx_sha256':hashlib.sha256(raw).hexdigest(),'original_envs_preserved':True,'deleted_files':0}
    private(ROOT/'fallbacks.json',json.dumps(proof).encode()); print(json.dumps({'prepared':True,'targets':[r['name']for r in rows],'live_routes_unchanged':True,'deleted_files':0}))


def apply(rollback=False):
    minimum=guard(); proof=json.loads((ROOT/'fallbacks.json').read_text()); before=markers()
    assert before==proof['protected'] and hashlib.sha256(PROXY.read_bytes()).hexdigest()==proof['nginx_sha256']
    if not rollback:
        assert subprocess.run(['systemctl','is-active','--quiet','greenmind-pgbouncer-fallback-20261011.service']).returncode==0
    completed=[]
    try:
        for row in proof['rows']:
            guard()
            base=Path(row['compose']).parent
            assert hashlib.sha256(Path(row['compose']).read_bytes()).hexdigest()==row['compose_sha256']
            assert hashlib.sha256((base/'application.env').read_bytes()).hexdigest()==row['source_env_sha256']
            assert hashlib.sha256(Path(row['env']).read_bytes()).hexdigest()==row['env_sha256']
            assert hashlib.sha256(Path(row['override']).read_bytes()).hexdigest()==row['override_sha256']
            assert hashlib.sha256(Path(row['rollback']).read_bytes()).hexdigest()==row['rollback_sha256']
            args=['docker','compose','-f',row['compose'],'-f',row['rollback']if rollback else row['override']]
            completed.append(row)
            result=subprocess.run(args+['up','-d','--no-deps','--no-build','--pull','never','--force-recreate','application'],capture_output=True,timeout=35)
            assert result.returncode==0, 'Fallback recreation failed; private logs withheld'
            deadline=time.monotonic()+20
            while True:
                try:
                    with urlopen(f'http://127.0.0.1:{row["port"]}/health',timeout=2) as response: assert response.status==200
                    break
                except Exception:
                    if time.monotonic()>deadline: raise RuntimeError('Fallback health deadline')
                    time.sleep(.5)
            image=subprocess.check_output(['docker','inspect','--format','{{.Image}}',row['name']],text=True,timeout=5).strip()
            assert image==row['image']
            assert markers()==before
            minimum=min(minimum,guard())
        assert hashlib.sha256(PROXY.read_bytes()).hexdigest()==proof['nginx_sha256']
    except Exception:
        if not rollback:
            for row in completed:
                subprocess.run(['docker','compose','-f',row['compose'],'-f',row['rollback'],'up','-d','--no-deps','--no-build','--pull','never','--force-recreate','application'],capture_output=True,timeout=35)
        raise
    result={'at':time.time(),'pooled':not rollback,'rollback_rehearsed':rollback,'protected_services_unchanged':True,'nginx_unchanged':True,'original_envs_preserved':True,'minimum_available_mib':minimum,'deleted_files':0}
    private(ROOT/('fallbacks-'+str(time.time_ns())+'.json'),json.dumps(result).encode()); print(json.dumps(result))


if __name__=='__main__':
    assert os.geteuid()==0 and os.uname().nodename=='Biolingo'
    parser=argparse.ArgumentParser(); parser.add_argument('action',choices=['prepare','activate','rollback']); action=parser.parse_args().action
    if action=='prepare': prepare()
    else: apply(action=='rollback')
