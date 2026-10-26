"""Prepare an inactive copy-only service; authority transfer remains separate."""
import hashlib
import json
import os
import pwd
import subprocess
from pathlib import Path

RUNTIME=Path('/opt/greenmind/archive-preparation/20261010-v42')
ROOT=Path('/etc/greenmind/archive-copy-20261010')
STATE=Path('/var/lib/greenmind/archive-copy-20261010')

def values(path):
    return dict(x.split('=',1)for x in path.read_text().splitlines()if x and not x.startswith('#'))

def main():
    assert os.geteuid()==0 and os.uname().nodename=='ubuntu-4gb-fsn1-1'
    assert not ROOT.exists() and not STATE.exists()
    for name,sha in json.loads((RUNTIME/'files.json').read_text()).items():
        assert hashlib.sha256((RUNTIME/name).read_bytes()).hexdigest()==sha
    account=pwd.getpwnam('greenmind-archive-copy')
    d=values(Path('/etc/greenmind/archive-offload-v10/access.env'))
    d.update(values(Path('/etc/greenmind/archive-source-read-v12/source-read.env')))
    for kind in ['GATEWAY','DIRECT']:
        assert d['RAW_ARCHIVE_'+kind+'_S3_ENDPOINT']=='http://127.0.0.1:9000'
    d.update(PYTHONPATH=str(RUNTIME/'backend'),RAW_ARCHIVE_ENABLED='true',
             RAW_ARCHIVE_DELETE_ENABLED='false',RAW_ARCHIVE_READS_ACCEPTED='false',
             RAW_ARCHIVE_STATE_DIR=str(STATE),RAW_ARCHIVE_MAX_SECONDS='75',
             RAW_ARCHIVE_MAX_FILES='100',RAW_ARCHIVE_MAX_REFERENCES='400',
             RAW_ARCHIVE_MAX_BYTES=str(64*1024**2),RAW_ARCHIVE_PAGE_SIZE='25',
             RAW_ARCHIVE_SETTLE_SECONDS='600',RAW_ARCHIVE_SFTP_KILOBITS='8192',
             RAW_ARCHIVE_COORDINATION_DIR='/var/lib/greenmind/archive-coordination')
    assert d['RAW_ARCHIVE_SOURCE_MIN_MEMORY_MIB']=='640'
    assert d['RAW_ARCHIVE_MIN_AVAILABLE_MEMORY_MIB']=='800'
    assert d['RAW_ARCHIVE_SOURCE_MAX_LOAD']=='2'
    assert d['RAW_ARCHIVE_MAX_HOST_LOAD']=='2'
    assert all(d[k]=='false'for k in ['RAW_ARCHIVE_DELETE_ENABLED','RAW_ARCHIVE_LEGACY_NULL_ACCEPTED',
                                    'RETENTION_ENABLED','DIRECT_RETENTION_ENABLED','VISUAL_PRUNE_ENABLED'])
    ROOT.mkdir(mode=0o750);os.chown(ROOT,0,account.pw_gid)
    STATE.mkdir(mode=0o700);os.chown(STATE,account.pw_uid,account.pw_gid)
    env=ROOT/'copy.env'
    with env.open('x')as out:
        os.fchmod(out.fileno(),0o640);os.fchown(out.fileno(),0,account.pw_gid)
        out.write(''.join(k+'='+v+'\n'for k,v in sorted(d.items())))
    unit=Path('/etc/systemd/system/greenmind-archive-copy.service')
    timer=Path('/etc/systemd/system/greenmind-archive-copy.timer')
    assert not unit.exists() and not timer.exists()
    unit.write_text('''[Unit]
Description=GreenMind archive-host copy-only worker
After=greenmind-source-link.service
ConditionPathExists=/etc/greenmind/archive-copy-owner.json
[Service]
Type=oneshot
User=greenmind-archive-copy
Group=greenmind-archive-copy
Slice=greenmind-archive.slice
EnvironmentFile='''+str(env)+'''
ExecStart=/opt/greenmind/archive-python-20261008-v4/bin/python -m app.raw_archive.archive_copy_once
MemoryHigh=128M
MemoryMax=192M
MemorySwapMax=0
CPUQuota=25%
TimeoutStartSec=110
TimeoutStopSec=15
SuccessExitStatus=75
Nice=19
IOSchedulingClass=idle
TasksMax=32
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths='''+str(STATE)+''' /var/lib/greenmind/archive-coordination
''')
    timer.write_text('''[Unit]
Description=GreenMind single-writer daily and catch-up copy
ConditionPathExists=/etc/greenmind/archive-copy-owner.json
[Timer]
OnCalendar=*-*-* 00:00:00 Europe/Zurich
OnCalendar=*-*-* *:0/2:00
AccuracySec=15s
Persistent=false
Unit=greenmind-archive-copy.service
[Install]
WantedBy=timers.target
''')
    for p in [unit,timer]:p.chmod(0o644)
    subprocess.run(['systemd-analyze','verify',str(unit),str(timer)],check=True,capture_output=True,timeout=15)
    subprocess.run(['systemctl','daemon-reload'],check=True,capture_output=True,timeout=15)
    assert not Path('/etc/greenmind/archive-copy-owner.json').exists()
    print(json.dumps({'status':'prepared_disabled','state':str(STATE),'runtime':str(RUNTIME),
                      'new_admin_identities':[],'deleted_files':0}))

if __name__=='__main__':main()
