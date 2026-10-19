"""Prepare private read-only API/broker. No public switch or receiver change."""
import hashlib
import json
import os
import pwd
import secrets
import subprocess
from pathlib import Path

RUNTIME=Path('/opt/greenmind/archive-preparation/20261010-v42')
ROOT=Path('/etc/greenmind/archive-reader-20261010')
STATE=Path('/var/lib/greenmind/archive-reader-20261010')
SOCKET=Path('/run/greenmind-archive-reader-20261010')

def env(path):
    return dict(x.split('=',1)for x in path.read_text().splitlines()if x and not x.startswith('#'))

def private(path,body,uid,gid,mode=0o600):
    with path.open('xb')as f:
        os.fchmod(f.fileno(),mode);os.fchown(f.fileno(),uid,gid);f.write(body)

def main():
    assert os.geteuid()==0 and os.uname().nodename=='ubuntu-4gb-fsn1-1'
    assert not ROOT.exists() and not STATE.exists()
    for name,sha in json.loads((RUNTIME/'files.json').read_text()).items():
        assert hashlib.sha256((RUNTIME/name).read_bytes()).hexdigest()==sha
    source=Path('/home/traver/archive-reader-access-20261010.env')
    assert hashlib.sha256(source.read_bytes()).hexdigest()=='7ad5b789365314c26392c89c6c3572cc53c34674c210a045364d41e8afda1dbc'
    read=env(source)
    assert 'JWT_SECRET_KEY'not in read and not any(k.startswith('RAW_ARCHIVE_SFTP_')for k in read)
    assert read['ARCHIVE_REMOTE_AUTH_ENABLED']=='true' and read['MANAGEMENT_ADMIN_EMAILS']
    a=pwd.getpwnam('greenmind-archive-read')
    ROOT.mkdir(mode=0o750);os.chown(ROOT,0,a.pw_gid)
    STATE.mkdir(mode=0o700);os.chown(STATE,a.pw_uid,a.pw_gid)
    for name in ['scratch','jobs']:
        p=STATE/name;p.mkdir(mode=0o700);os.chown(p,a.pw_uid,a.pw_gid)
    runtime={
        'PYTHONPATH':str(RUNTIME/'backend'),'PYTHONHOME':'/opt/greenmind/archive-python-20261008-v4',
        'LD_LIBRARY_PATH':'/opt/greenmind/archive-python-20261008-v4/lib','PYTHONDONTWRITEBYTECODE':'1',
        'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1','PYTHONUNBUFFERED':'1',
        'RAW_ARCHIVE_READ_BROKER_SOCKET':str(SOCKET/'broker.sock'),
        'RAW_ARCHIVE_READ_SCRATCH_DIR':str(STATE/'scratch'),
        'RAW_ARCHIVE_STATE_DIR':'/var/lib/greenmind/archive-copy-20261010',
        'RAW_ARCHIVE_ENVIRONMENT':'production','RAW_ARCHIVE_DELETE_ENABLED':'false',
        'RAW_ARCHIVE_LEGACY_NULL_ACCEPTED':'false','RETENTION_ENABLED':'false',
        'DIRECT_RETENTION_ENABLED':'false','VISUAL_PRUNE_ENABLED':'false',
        'RAW_ARCHIVE_READS_ENABLED':'true','RAW_ARCHIVE_READS_ACCEPTED':'true',
        'RAW_ARCHIVE_ENABLED':'false','RAW_ARCHIVE_DIRECT_S3_BUCKET':'greenmind-direct-production-hotspot',
    }
    read.update(runtime,JWT_SECRET_KEY=secrets.token_hex(32),
                ARCHIVE_EXPORT_STATE_DIR=str(STATE/'jobs'),VISUAL_ARCHIVE_DIR=str(STATE/'visual'),
                DIRECT_INGEST_ENABLED='false',RELEASE_ID='archive-reader-20261010-v42')
    # This key is unused: source validation remains mandatory for user authentication.
    private(ROOT/'read.env',''.join(k+'='+v+'\n'for k,v in sorted(read.items())).encode(),0,a.pw_gid,0o640)
    broker=env(Path('/etc/greenmind/archive-reader-validation-v10/read.env'))
    broker.update(runtime)
    full=env(Path('/etc/greenmind/archive-offload-v10/access.env'))
    for key in ['RAW_ARCHIVE_SOURCE_PROBE_URL','RAW_ARCHIVE_SOURCE_ID','RAW_ARCHIVE_SOURCE_MIN_MEMORY_MIB',
                'RAW_ARCHIVE_SOURCE_MAX_LOAD','RAW_ARCHIVE_MIN_AVAILABLE_MEMORY_MIB','RAW_ARCHIVE_MAX_HOST_LOAD',
                'RAW_ARCHIVE_HEALTH_URLS','RAW_ARCHIVE_SOURCE_MOUNTS']:
        broker[key]=full[key]
    token=Path(full['RAW_ARCHIVE_SOURCE_PROBE_TOKEN_FILE'])
    private(ROOT/'probe-token',token.read_bytes(),a.pw_uid,a.pw_gid)
    broker.update(RAW_ARCHIVE_WORKER_ROLE='archive',RAW_ARCHIVE_SOURCE_PROBE_TOKEN_FILE=str(ROOT/'probe-token'),
                  RAW_ARCHIVE_AUTH_SOURCE_URL='http://127.0.0.1:18120/api/v1/auth/me')
    assert not any(k.startswith('RAW_ARCHIVE_SFTP_')for k in broker)
    private(ROOT/'broker.env',''.join(k+'='+v+'\n'for k,v in sorted(broker.items())).encode(),0,a.pw_gid,0o640)
    for name,ram,execstart,writable in [
        ('broker','96M','-m app.raw_archive.read_broker',str(STATE/'scratch')+' '+str(SOCKET)),
        ('api','192M','-m uvicorn app.visualization.api:app --host 127.0.0.1 --port 8141 --workers 1 --no-access-log --limit-concurrency 8',str(STATE/'jobs'))]:
        unit=Path('/etc/systemd/system/greenmind-archive-reader-'+name+'-20261010.service')
        assert not unit.exists()
        content='''[Unit]
Description=GreenMind private read-only archive '''+name+'''
After=greenmind-source-link.service
[Service]
Type=simple
User=greenmind-archive-read
Group=greenmind-archive-read
Slice=greenmind-archive.slice
EnvironmentFile='''+str(ROOT/('broker.env'if name=='broker'else'read.env'))+'''
ExecStart=/opt/greenmind/archive-python-20261008-v4/bin/python '''+execstart+'''
MemoryMax='''+ram+'''
MemorySwapMax=0
CPUQuota=25%
TasksMax=32
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths='''+writable+'''
Restart=no
'''
        if name=='broker':content+='RuntimeDirectory='+SOCKET.name+'\nRuntimeDirectoryMode=0700\n'
        unit.write_text(content);unit.chmod(0o644)
        subprocess.run(['systemd-analyze','verify',str(unit)],check=True,capture_output=True,timeout=10)
    subprocess.run(['setfacl','-m','u:greenmind-archive-read:--x',str(ROOT.parent)],check=True,timeout=5)
    subprocess.run(['systemctl','daemon-reload'],check=True,capture_output=True,timeout=15)
    print(json.dumps({'status':'prepared_inactive','public_routes_changed':False,
                      'jwt_production_secret_exported':False,'upload_access_mounted':False,
                      'existing_configured_admin_policy_preserved':True,'deleted_files':0}))

if __name__=='__main__':main()
