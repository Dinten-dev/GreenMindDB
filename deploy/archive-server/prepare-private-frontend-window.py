"""Root-only finite private frontend acceptance with independent rollback.

Only the reviewed candidate can be stopped. Public routes and existing
containers are never changed. Both scripts live in a root-owned directory.
"""
import hashlib
import json
import os
import subprocess
import tarfile
from pathlib import Path

ROOT=Path('/opt/greenmind/peter-frontend-v2-20261010')
PACKAGE=Path('/home/traver/greenmind-peter-frontend-v2-20261010/frontend-activation-handoff-v2.tar.gz')
PACKAGE_SHA='e7ec1fd404484cf70431108990471bf21fd9c44589d71ccf3532313f57dd49b2'
PROXY_SHA='b5dbe4d009cce995c31bb7571413667dfe43130f051ec903d817b5c67d434d6c'
START_SHA='4c382a92a3f5dbf775068c76d2fab954a1deeb076eef27b337cefe7ecf87907f'

WAIT=r'''import hashlib,json,os,subprocess,time
from pathlib import Path
ROOT=Path('/opt/greenmind/peter-frontend-v2-20261010')
PIN='b5dbe4d009cce995c31bb7571413667dfe43130f051ec903d817b5c67d434d6c'
START='4c382a92a3f5dbf775068c76d2fab954a1deeb076eef27b337cefe7ecf87907f'
assert os.geteuid()==0 and os.uname().nodename=='Biolingo'
deadline=time.monotonic()+3500
while True:
    assert hashlib.sha256(Path('/etc/nginx/sites-available/greenmind-prod').read_bytes()).hexdigest()==PIN
    assert hashlib.sha256((ROOT/'start_frontend.py').read_bytes()).hexdigest()==START
    available=int(next(l.split()[1]for l in Path('/proc/meminfo').read_text().splitlines()if l.startswith('MemAvailable:')))/1024
    if available>=928 and os.getloadavg()[0]<=2.4:break
    if time.monotonic()>=deadline:raise SystemExit(75)
    time.sleep(20)
subprocess.run(['systemd-run','--unit=greenmind-private-frontend-v2-watchdog','--on-active=240s','--timer-property=AccuracySec=1s','-p','MemoryMax=32M','-p','MemorySwapMax=0','-p','CPUQuota=5%','-p','TimeoutStartSec=30','/usr/bin/python3',str(ROOT/'watchdog.py')],check=True,timeout=10)
result=subprocess.run(['/usr/bin/python3',str(ROOT/'start_frontend.py')],timeout=210)
assert result.returncode==0
proof=json.loads((ROOT/'private-frontend.json').read_text())
assert proof['private_pages_healthy'] and proof['reserve_min_bytes']>=640*1024**2 and proof['load_max']<=2.4
assert hashlib.sha256(Path('/etc/nginx/sites-available/greenmind-prod').read_bytes()).hexdigest()==PIN
print(json.dumps({'private_frontend_accepted':True,'public_switch':False,'independent_rollback_armed':True,'deleted_files':0}))
'''

WATCH=r'''import hashlib,http.client,json,os,socket
from pathlib import Path
ROOT=Path('/opt/greenmind/peter-frontend-v2-20261010')
NAME='gm-peter-frontend-20261010'
IMAGE='sha256:5f216424960b887e6a6b6856bd1f42f187a84c0adc5875ec54b97e97ebafc639'
assert os.geteuid()==0 and os.uname().nodename=='Biolingo'
proxy=Path('/etc/nginx/sites-available/greenmind-prod').read_text()
if '127.0.0.1:3142' in proxy:raise SystemExit('Candidate publicly referenced: private rollback refused')
proof=ROOT/'private-frontend.json'
if proof.exists():
    value=json.loads(proof.read_text())
    if value.get('private_pages_healthy') and value.get('image')==IMAGE and value.get('load_max',99)<=2.4 and value.get('reserve_min_bytes',0)>=640*1024**2:
        print(json.dumps({'action':'retain_accepted_private_candidate','deleted_files':0}));raise SystemExit(0)
class Docker(http.client.HTTPConnection):
    def __init__(self):super().__init__('localhost',timeout=15)
    def connect(self):self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);self.sock.settimeout(15);self.sock.connect('/var/run/docker.sock')
c=Docker();c.request('GET','/v1.44/containers/'+NAME+'/json');r=c.getresponse();body=r.read();c.close()
if r.status==404:raise SystemExit(0)
assert r.status==200
value=json.loads(body);assert value['Image']==IMAGE
if value['State']['Running']:
    c=Docker();c.request('POST','/v1.44/containers/'+value['Id']+'/stop?t=10');r=c.getresponse();r.read();assert r.status in (204,304);c.close()
print(json.dumps({'action':'stop_unaccepted_private_candidate','original_containers_unchanged':True,'deleted_files':0}))
'''

def main():
    assert os.geteuid()==0 and os.uname().nodename=='Biolingo'
    assert hashlib.sha256(PACKAGE.read_bytes()).hexdigest()==PACKAGE_SHA
    assert hashlib.sha256(Path('/etc/nginx/sites-available/greenmind-prod').read_bytes()).hexdigest()==PROXY_SHA
    assert not ROOT.exists()
    with tarfile.open(PACKAGE)as archive:
        members=archive.getmembers()
        assert len(members)==6 and all(m.isfile()and Path(m.name).name==m.name for m in members)
        handoff=json.load(archive.extractfile('handoff.json'))
        assert set(handoff['files'])|{'handoff.json'}=={m.name for m in members}
        ROOT.mkdir(mode=0o755)
        for member in members:
            raw=archive.extractfile(member).read()
            if member.name!='handoff.json':assert hashlib.sha256(raw).hexdigest()==handoff['files'][member.name]
            path=ROOT/member.name;path.write_bytes(raw);path.chmod(0o644)
    for name,body in [('await-private-frontend.py',WAIT),('watchdog.py',WATCH)]:
        compile(body,name,'exec');path=ROOT/name;path.write_text(body);path.chmod(0o644)
    assert hashlib.sha256((ROOT/'start_frontend.py').read_bytes()).hexdigest()==START_SHA
    subprocess.run(['systemd-run','--unit=greenmind-await-private-frontend-v2','--no-block','-p','Type=oneshot','-p','MemoryMax=32M','-p','MemorySwapMax=0','-p','CPUQuota=5%','-p','TimeoutStartSec=3800','-p','SuccessExitStatus=75','/usr/bin/python3',str(ROOT/'await-private-frontend.py')],check=True,timeout=10)
    print(json.dumps({'guarded_private_frontend_wait_started':True,'maximum_wait_seconds':3500,'public_switch':False,'deleted_files':0}))

if __name__=='__main__':main()
