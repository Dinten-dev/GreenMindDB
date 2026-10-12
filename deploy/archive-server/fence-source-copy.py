"""Fence only the old copier; an uncertain handoff fails closed."""
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

MARKER=Path('/etc/greenmind/archive-copy-owner.json')
DROP=Path('/etc/systemd/system/greenmind-raw-copy.service.d/zzzz-archive-owner-fence.conf')
UNITS=('greenmind-raw-copy.timer','greenmind-raw-copy-drain.timer','greenmind-raw-copy.service')
RECEIVERS=('gm-zones-production-2e687bec010a-application-1',
           'gm-peter-receiver-20261010','gm-direct-production-direct-api-1','greenminddb-backend-1')

def command(args,timeout=25):
    return subprocess.run(args,check=True,capture_output=True,text=True,timeout=timeout).stdout

def receivers():
    values=json.loads(command(['docker','inspect',*RECEIVERS]))
    return [{'name':v['Name'],'image':v['Image'],'started':v['State']['StartedAt'],
             'restarts':v['RestartCount'],'running':v['State']['Running']}for v in values]

def main():
    assert os.geteuid()==0 and os.uname().nodename=='Biolingo'
    assert not MARKER.exists() and not DROP.exists()
    assert all(v['running'] for v in receivers())
    before=receivers()
    stamp=datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f')
    root=Path('/opt/greenmind/copy-handoff')/stamp
    root.mkdir(parents=True,mode=0o700)
    original={'receivers':before,'timers':{u:command(['systemctl','is-enabled',u]).strip()for u in UNITS[:2]},
              'proxy_sha256':hashlib.sha256(Path('/etc/nginx/sites-available/greenmind-prod').read_bytes()).hexdigest()}
    with (root/'original.json').open('x')as out:
        os.fchmod(out.fileno(),0o600);json.dump(original,out)
    with MARKER.open('x')as out:
        os.fchmod(out.fileno(),0o644)
        json.dump({'schema':1,'owner':'handoff_pending','accepted':False,'environment':'production',
                   'at':datetime.now(UTC).isoformat(),'evidence':str(root),'deletion_enabled':False},out)
    with DROP.open('x')as out:
        os.fchmod(out.fileno(),0o644)
        out.write('[Unit]\nConditionPathExists=!'+str(MARKER)+'\n')
    command(['systemctl','daemon-reload'])
    command(['systemctl','stop',*UNITS],timeout=35)
    assert all(command(['systemctl','show',u,'--value','-p','ActiveState']).strip() in {'inactive','failed'}for u in UNITS)
    command(['systemctl','reset-failed',UNITS[-1]])
    command(['systemctl','start',UNITS[-1]])
    assert command(['systemctl','show',UNITS[-1],'--value','-p','ConditionResult']).strip()=='no'
    assert receivers()==before
    assert hashlib.sha256(Path('/etc/nginx/sites-available/greenmind-prod').read_bytes()).hexdigest()==original['proxy_sha256']
    proof={'status':'source_copy_fenced','at':datetime.now(UTC).isoformat(),'receivers_unchanged':True,
           'original_journal_retained':True,'archive_writer_started':False,'deleted_files':0,
           'restart_test_denied':True,'evidence':str(root)}
    with (root/'fenced.json').open('x')as out:
        os.fchmod(out.fileno(),0o600);json.dump(proof,out)
    print(json.dumps(proof))

if __name__=='__main__':main()
