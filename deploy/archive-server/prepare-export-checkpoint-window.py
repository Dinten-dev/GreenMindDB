"""Await capacity and accepted private reader before export-owner checkpoint."""
import hashlib
import json
import os
import subprocess
from pathlib import Path

ROOT=Path('/opt/greenmind/export-handoff-20261010-v3')
SOURCE=Path('/home/traver/snapshot-source-export-jobs.py')

def main():
    assert os.geteuid()==0 and os.uname().nodename=='Biolingo'
    assert not ROOT.exists()
    raw=SOURCE.read_bytes();digest=hashlib.sha256(raw).hexdigest()
    ROOT.mkdir(mode=0o700)
    target=ROOT/'checkpoint.py';target.write_bytes(raw);target.chmod(0o600)
    wait='''import hashlib,json,os,subprocess,time
from pathlib import Path
from urllib.request import urlopen
ROOT=Path('/opt/greenmind/export-handoff-20261010-v3')
TARGET=ROOT/'checkpoint.py'
assert os.geteuid()==0 and os.uname().nodename=='Biolingo'
deadline=time.monotonic()+3500
while True:
    assert hashlib.sha256(TARGET.read_bytes()).hexdigest()==DIGEST
    available=int(next(l.split()[1]for l in Path('/proc/meminfo').read_text().splitlines()if l.startswith('MemAvailable:')))/1024
    ready=False
    if available>=688 and os.getloadavg()[0]<=2.4:
        try:
            with urlopen('http://127.0.0.1:28141/health',timeout=3)as r:value=json.loads(r.read(8192))
            ready=value.get('release_id')=='archive-reader-20261010-v48'
        except Exception:pass
    if ready:
        result=subprocess.run(['/usr/bin/python3',str(TARGET)],capture_output=True,timeout=90)
        if result.returncode!=75:break
        ready=False
    if time.monotonic()>=deadline:raise SystemExit(75)
    time.sleep(20)
proof={'exit':result.returncode,'stdout':result.stdout.decode()[-8192:],'deleted_files':0}
with (ROOT/'result.json').open('x')as out:
    os.fchmod(out.fileno(),0o600);json.dump(proof,out)
print(json.dumps(proof))
raise SystemExit(result.returncode)
'''.replace('DIGEST',repr(digest))
    compile(wait,'await-checkpoint.py','exec')
    control=ROOT/'await-checkpoint.py';control.write_text(wait);control.chmod(0o600)
    subprocess.run(['systemd-run','--unit=greenmind-await-export-checkpoint-v3','--no-block','-p','Type=oneshot','-p','MemoryMax=48M','-p','MemorySwapMax=0','-p','CPUQuota=5%','-p','TimeoutStartSec=3650','-p','SuccessExitStatus=75','/usr/bin/python3',str(control)],check=True,timeout=10)
    print(json.dumps({'bounded_checkpoint_wait_started':True,'maximum_wait_seconds':3500,'receivers_changed':False,'deleted_files':0}))

if __name__=='__main__':main()
