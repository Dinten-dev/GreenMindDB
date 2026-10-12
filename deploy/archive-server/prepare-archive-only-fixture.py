"""Two approved S3 misses through the private reader, with independent restore.

No requests are forwarded to MinIO. Source JWTs remain on Source. The fixture
returns NoSuchKey only for the exact two approved paths and rejects all writes.
"""
import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

ROOT=Path('/opt/greenmind/archive-validation/archive-only-v48')
PROFILE=Path('/etc/greenmind/archive-reader-20261010/read.env')
UNIT='greenmind-archive-reader-api-20261010.service'

SERVER=r'''import json,os,ssl
from http.server import BaseHTTPRequestHandler,HTTPServer
from pathlib import Path
from urllib.parse import unquote,urlsplit
ROOT=Path('/opt/greenmind/archive-validation/archive-only-v48')
selected=json.loads((ROOT/'locations.json').read_text());counts=dict.fromkeys(selected,0)
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        path=unquote(urlsplit(self.path).path)
        kind=next((k for k,v in selected.items()if v['object']==path),None)
        if kind is None:self.send_error(403);return
        counts[kind]+=1
        with (ROOT/'requests.json').open('w')as out:os.fchmod(out.fileno(),0o600);json.dump(counts,out)
        body=b'<Error><Code>NoSuchKey</Code><Message>Private acceptance fixture</Message></Error>'
        self.send_response(404);self.send_header('Content-Type','application/xml');self.send_header('Content-Length',str(len(body)));self.end_headers()
        if self.command!='HEAD':self.wfile.write(body)
    do_HEAD=do_GET
    def do_POST(self):self.send_error(405)
    do_PUT=do_POST
    do_DELETE=do_POST
server=HTTPServer(('127.0.0.1',19445),Handler)
context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(ROOT/'ca.pem',ROOT/'key.pem')
server.socket=context.wrap_socket(server.socket,server_side=True);server.serve_forever()
'''

RESTORE=r'''import hashlib,json,os,subprocess,time
from pathlib import Path
from urllib.request import urlopen
ROOT=Path('/opt/greenmind/archive-validation/archive-only-v48')
PROFILE=Path('/etc/greenmind/archive-reader-20261010/read.env')
UNIT='greenmind-archive-reader-api-20261010.service'
assert os.geteuid()==0 and os.uname().nodename=='ubuntu-4gb-fsn1-1'
info=json.loads((ROOT/'restore.json').read_text());raw=(ROOT/'before.env').read_bytes()
assert hashlib.sha256(raw).hexdigest()==info['original_sha256']
current=hashlib.sha256(PROFILE.read_bytes()).hexdigest()
assert current in (info['original_sha256'],info['fault_sha256']),'Concurrent private reader edit'
subprocess.run(['systemctl','stop',UNIT],check=True,timeout=20)
with PROFILE.open('wb')as out:out.write(raw);out.flush();os.fsync(out.fileno())
deadline=time.monotonic()+180
data=dict(l.split('=',1)for l in Path('/etc/greenmind/archive-reader-20261010/broker.env').read_text().splitlines()if l and not l.startswith('#'))
import secrets
from urllib.request import Request
while True:
    memory=int(next(l.split()[1]for l in Path('/proc/meminfo').read_text().splitlines()if l.startswith('MemAvailable:')))/1024
    ready=False
    if memory>=1088 and os.getloadavg()[0]<=2:
        nonce=secrets.token_hex(16);began=time.monotonic()
        try:
            req=Request(data['RAW_ARCHIVE_SOURCE_PROBE_URL'],headers={'Authorization':'Bearer '+Path(data['RAW_ARCHIVE_SOURCE_PROBE_TOKEN_FILE']).read_text().strip(),'X-GreenMind-Probe-Nonce':nonce})
            with urlopen(req,timeout=3)as r:value=json.loads(r.read(8192))
            ready=value['probe_nonce']==nonce and 0<=value['sample_age_seconds']+time.monotonic()-began<=10 and value['available_mib']>=640 and value['load']<=2 and value['routes']=={'8120':200,'8003':200,'8000':200}
        except Exception:pass
    if ready:break
    if time.monotonic()>=deadline:raise SystemExit(75)
    time.sleep(5)
subprocess.run(['systemctl','start',UNIT],check=True,timeout=20)
for _ in range(30):
    try:
        with urlopen('http://127.0.0.1:8141/health',timeout=2)as r:value=json.loads(r.read(8192))
        assert value['release_id']=='archive-reader-20261010-v48';break
    except Exception:time.sleep(1)
else:raise SystemExit(1)
print(json.dumps({'private_configuration_restored':True,'private_health':True,'public_routes_changed':False,'deleted_files':0}))
'''

def command(arguments,timeout=20):
    return subprocess.run(arguments,check=True,capture_output=True,timeout=timeout)

def main():
    assert os.geteuid()==0 and os.uname().nodename=='ubuntu-4gb-fsn1-1'
    parser=argparse.ArgumentParser();parser.add_argument('--activate',action='store_true');args=parser.parse_args()
    if args.activate:
        info=json.loads((ROOT/'restore.json').read_text())
        assert hashlib.sha256(PROFILE.read_bytes()).hexdigest()==info['original_sha256']
        # Check original immutable broker guard before touching the private API.
        profile=Path('/etc/greenmind/archive-reader-20261010/broker.env')
        data=dict(l.split('=',1)for l in profile.read_text().splitlines()if l and not l.startswith('#'))
        code='import json;from app.raw_archive.runner import health_probe;p=health_probe();ok=p();p.close();print(json.dumps({"ready":ok}));raise SystemExit(0 if ok else 75)'
        command(['systemd-run','--wait','--pipe','--unit=greenmind-archive-only-v48-admission','-p','User=greenmind-archive-read','-p','Group=greenmind-archive-read','-p','Slice=greenmind-archive.slice','-p','EnvironmentFile='+str(profile),'-p','MemoryMax=64M','-p','MemorySwapMax=0','-p','CPUQuota=5%','-p','TimeoutStartSec=15','/opt/greenmind/archive-python-20261008-v4/bin/python','-c',code],25)
        command(['systemd-run','--unit=greenmind-archive-only-v48-restore','--on-active=1200s','--timer-property=AccuracySec=1s','-p','MemoryMax=64M','-p','MemorySwapMax=0','-p','CPUQuota=5%','-p','TimeoutStartSec=240','-p','SuccessExitStatus=75','/usr/bin/python3',str(ROOT/'restore.py')])
        command(['systemctl','stop',UNIT])
        with PROFILE.open('wb')as out:out.write((ROOT/'fault.env').read_bytes());out.flush();os.fsync(out.fileno())
        command(['systemctl','start',UNIT])
        print(json.dumps({'private_fixture_active':True,'automatic_restore_seconds':1200,'public_routes_changed':False,'source_objects_removed':0,'deleted_files':0}));return
    assert not ROOT.exists()
    selected=Path('/home/traver/approved-archive-only-locations-v48.json')
    assert selected.is_file()and not selected.is_symlink()
    value=json.loads(selected.read_text());assert set(value)=={'gateway','direct'}
    assert value['gateway']['sha256']=='6bf907bbbe43231db74d095c6ee9f0782bca3a3430bb2d2db64ccee25bdcdab9'
    assert value['direct']['sha256']=='03286f6f51635170f6dc5f1f7c431b419e0c1cf0f7784b129d651168ce7d0407'
    ROOT.mkdir(mode=0o755)
    def write(name,raw,mode=0o600):
        with (ROOT/name).open('xb')as out:os.fchmod(out.fileno(),mode);out.write(raw)
    write('locations.json',selected.read_bytes());write('before.env',PROFILE.read_bytes())
    original=PROFILE.read_text();assert original.count('S3_ENDPOINT=')==1 and original.count('DIRECT_S3_ENDPOINT_URL=')==1
    changed='\n'.join(l.split('=',1)[0]+'=https://127.0.0.1:19445'if l.startswith(('S3_ENDPOINT=','DIRECT_S3_ENDPOINT_URL='))else l for l in original.splitlines()if not l.startswith('AWS_CA_BUNDLE='))+'\nAWS_CA_BUNDLE='+str(ROOT/'ca.pem')+'\n'
    write('fault.env',changed.encode())
    write('restore.json',json.dumps({'original_sha256':hashlib.sha256(original.encode()).hexdigest(),'fault_sha256':hashlib.sha256(changed.encode()).hexdigest()}).encode())
    for name,body in [('server.py',SERVER),('restore.py',RESTORE)]:compile(body,name,'exec');write(name,body.encode(),0o644)
    command(['openssl','req','-x509','-newkey','ec','-pkeyopt','ec_paramgen_curve:P-256','-nodes','-keyout',str(ROOT/'key.pem'),'-out',str(ROOT/'ca.pem'),'-days','2','-subj','/CN=127.0.0.1','-addext','subjectAltName=IP:127.0.0.1'])
    (ROOT/'key.pem').chmod(0o600);(ROOT/'ca.pem').chmod(0o644)
    command(['systemd-run','--unit=greenmind-archive-only-v48-fixture','--no-block','-p','Type=simple','-p','MemoryMax=64M','-p','MemorySwapMax=0','-p','CPUQuota=5%','-p','RuntimeMaxSec=1800','/usr/bin/python3',str(ROOT/'server.py')])
    print(json.dumps({'prepared':True,'approved_objects':2,'public_listener':False,'source_requests_forwarded':False,'deleted_files':0}))

if __name__=='__main__':main()
