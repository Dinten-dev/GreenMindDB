"""Prepare one inactive private export worker, never enable a schedule here."""
import json
import os
import pwd
import subprocess
from pathlib import Path

ROOT=Path('/etc/greenmind/archive-export-20261010')
STATE=Path('/var/lib/greenmind/archive-reader-20261010')
READER=Path('/etc/greenmind/archive-reader-20261010')
UNIT='greenmind-archive-export-20261010'

def env(path):
    return dict(l.split('=',1)for l in path.read_text().splitlines()if l and not l.startswith('#'))

def private(path,raw,uid,gid,mode=0o600):
    with path.open('xb')as out:
        os.fchmod(out.fileno(),mode);os.fchown(out.fileno(),uid,gid);out.write(raw)

def main():
    assert os.geteuid()==0 and os.uname().nodename=='ubuntu-4gb-fsn1-1'
    assert not ROOT.exists()
    reader=pwd.getpwnam('greenmind-archive-read')
    assert reader.pw_uid==995
    data=env(READER/'read.env');broker=env(READER/'broker.env')
    copy=env(Path('/etc/greenmind/archive-copy-20261010/copy.env'))
    assert not any(k.startswith('RAW_ARCHIVE_SFTP_')for k in data)
    assert data['RAW_ARCHIVE_DELETE_ENABLED']=='false' and data['ARCHIVE_REMOTE_AUTH_ENABLED']=='true'
    ROOT.mkdir(mode=0o750);os.chown(ROOT,0,reader.pw_gid)
    # The existing forced command grants only a two-minute source lease.
    # It grants no shell, SQL, object write, upload or administrative access.
    for key,name in [('RAW_ARCHIVE_LEASE_KEY','lease-key'),('RAW_ARCHIVE_LEASE_KNOWN_HOSTS','known-hosts')]:
        source=Path(copy[key]);assert source.is_file()and not source.is_symlink()and not source.stat().st_mode&0o077
        private(ROOT/name,source.read_bytes(),reader.pw_uid,reader.pw_gid)
        data[key]=str(ROOT/name)
    for key in ['RAW_ARCHIVE_LEASE_HOST','RAW_ARCHIVE_LEASE_USER','RAW_ARCHIVE_COORDINATION_DIR']:
        data[key]=copy[key]
    for key in ['RAW_ARCHIVE_SOURCE_PROBE_URL','RAW_ARCHIVE_SOURCE_ID','RAW_ARCHIVE_SOURCE_MIN_MEMORY_MIB','RAW_ARCHIVE_SOURCE_MAX_LOAD','RAW_ARCHIVE_MIN_AVAILABLE_MEMORY_MIB','RAW_ARCHIVE_MAX_HOST_LOAD','RAW_ARCHIVE_HEALTH_URLS','RAW_ARCHIVE_SOURCE_MOUNTS']:
        data[key]=broker[key]
    private(ROOT/'probe-token',Path(broker['RAW_ARCHIVE_SOURCE_PROBE_TOKEN_FILE']).read_bytes(),reader.pw_uid,reader.pw_gid)
    data.update(RAW_ARCHIVE_SOURCE_PROBE_TOKEN_FILE=str(ROOT/'probe-token'),
        RAW_ARCHIVE_READ_SCRATCH_DIR=str(STATE/'scratch/export'),
        RAW_ARCHIVE_WORKER_ROLE='archive',ARCHIVE_EXPORTS_ENABLED='true',
        RAW_ARCHIVE_ENABLED='false',RAW_ARCHIVE_DELETE_ENABLED='false',
        RETENTION_ENABLED='false',DIRECT_RETENTION_ENABLED='false',VISUAL_PRUNE_ENABLED='false')
    assert float(data['RAW_ARCHIVE_SOURCE_MIN_MEMORY_MIB'])>=640
    assert float(data['RAW_ARCHIVE_MIN_AVAILABLE_MEMORY_MIB'])>=800
    assert float(data['RAW_ARCHIVE_SOURCE_MAX_LOAD'])<=2.4
    assert data['RAW_ARCHIVE_LEASE_HOST']=='188.245.247.156'
    assert data['RAW_ARCHIVE_LEASE_USER']=='greenmind-archive-lease'
    assert not any(k.startswith('RAW_ARCHIVE_SFTP_')for k in data)
    private(ROOT/'export.env',''.join(k+'='+v+'\n'for k,v in sorted(data.items())).encode(),0,reader.pw_gid,0o640)
    unit=Path('/etc/systemd/system/'+UNIT+'.service')
    timer=Path('/etc/systemd/system/'+UNIT+'.timer')
    assert not unit.exists()and not timer.exists()
    content='''[Unit]
Description=GreenMind bounded archive-host export worker
After=greenmind-source-link.service greenmind-archive-reader-broker-20261010.service
ConditionPathExists=/etc/greenmind/archive-export-20261010/ownership-accepted.json
[Service]
Type=oneshot
User=greenmind-archive-read
Group=greenmind-archive-read
Slice=greenmind-archive.slice
EnvironmentFile=/etc/greenmind/archive-export-20261010/export.env
ExecStart=/opt/greenmind/archive-python-20261008-v4/bin/python -m app.raw_archive.archive_export_once
MemoryMax=128M
MemorySwapMax=0
CPUQuota=25%
Nice=19
IOSchedulingClass=idle
TasksMax=32
TimeoutStartSec=110
SuccessExitStatus=75
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/greenmind/archive-reader-20261010/jobs /var/lib/greenmind/archive-reader-20261010/scratch/export /var/lib/greenmind/archive-coordination
InaccessiblePaths=/etc/greenmind/archive-copy-20261010 /etc/greenmind/archive-offload-v10 /etc/greenmind/archive-source-read-v12 /etc/greenmind/archive-diagnostic-read-20261010 /etc/greenmind/archive-reader-validation-v10
'''
    private(unit,content.encode(),0,0,0o644)
    private(timer,b'''[Unit]
Description=GreenMind private bounded export schedule
[Timer]
OnBootSec=2min
OnUnitInactiveSec=30s
AccuracySec=5s
Unit=greenmind-archive-export-20261010.service
[Install]
WantedBy=timers.target
''',0,0,0o644)
    subprocess.run(['systemd-analyze','verify',str(unit),str(timer)],check=True,capture_output=True,timeout=10)
    # Existing broker namespaces predate this directory. Require explicit
    # private restart and negative access acceptance before writer ownership.
    private(ROOT/'namespace-refresh-required.json',json.dumps({
        'required':True,'path':str(ROOT),'writer_ownership_accepted':False,
        'deleted_files':0}).encode(),0,0)
    subprocess.run(['systemctl','daemon-reload'],check=True,timeout=15)
    print(json.dumps({'prepared_inactive':True,'memory_max_mib':128,'upload_access':False,'schedules_started':False,'deleted_files':0}))

if __name__=='__main__':main()
