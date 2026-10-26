"""Refresh only the unpublished broker's masks after export provisioning."""
import hashlib,json,os,secrets,subprocess,time
from pathlib import Path
from urllib.request import Request,urlopen
ROOT=Path('/opt/greenmind/archive-validation/credential-mask-refresh-v48')
CONFIG=Path('/etc/greenmind/archive-reader-20261010')
EXPORT=Path('/etc/greenmind/archive-export-20261010')
UNITS=['greenmind-archive-reader-api-20261010.service','greenmind-archive-reader-broker-20261010.service']
def actual_masks():
    blocked=[Path('/etc/greenmind/archive-copy-20261010/copy.env'),Path('/etc/greenmind/archive-offload-v10/access.env'),EXPORT/'export.env',EXPORT/'lease-key']
    result={}
    for unit in UNITS:
        pid=subprocess.check_output(['systemctl','show',unit,'--value','-p','MainPID'],timeout=5).decode().strip();assert int(pid)>0
        for path in blocked:
            r=subprocess.run(['nsenter','-t',pid,'-m','--','setpriv','--reuid=995','--regid=982','--clear-groups','/usr/bin/test','-r',str(path)],capture_output=True,timeout=5)
            assert r.returncode==1 and not r.stderr,'Actual credential mask missing'
        result[unit]={'blocked_paths':len(blocked),'passed':True}
    return result
def main():
    assert os.geteuid()==0 and os.uname().nodename=='ubuntu-4gb-fsn1-1'
    assert EXPORT.is_dir()and not (EXPORT/'ownership-accepted.json').exists()and not ROOT.exists()
    env=dict(l.split('=',1)for l in (CONFIG/'broker.env').read_text().splitlines()if l and not l.startswith('#'))
    nonce=secrets.token_hex(16);began=time.monotonic()
    req=Request(env['RAW_ARCHIVE_SOURCE_PROBE_URL'],headers={'Authorization':'Bearer '+Path(env['RAW_ARCHIVE_SOURCE_PROBE_TOKEN_FILE']).read_text().strip(),'X-GreenMind-Probe-Nonce':nonce})
    with urlopen(req,timeout=3)as response:v=json.loads(response.read(8193))
    assert v['probe_nonce']==nonce and 0<=v['sample_age_seconds']+time.monotonic()-began<=10 and v['available_mib']>=640 and v['load']<=2 and v['routes']=={'8120':200,'8003':200,'8000':200}
    assert int(next(l.split()[1]for l in Path('/proc/meminfo').read_text().splitlines()if l.startswith('MemAvailable:')))/1024>=896 and os.getloadavg()[0]<=2
    with urlopen('http://127.0.0.1:8141/health',timeout=3)as r:assert json.loads(r.read(8192))['release_id']=='archive-reader-20261010-v48'
    ROOT.mkdir(mode=0o700)
    for unit in UNITS:
        p=Path('/etc/systemd/system')/(unit+'.d/private-credential-isolation.conf');old=p.read_bytes()
        assert old.count(b'-/etc/greenmind/archive-export-20261010')==1
        (ROOT/(unit+'.before.conf')).write_bytes(old);(ROOT/(unit+'.before.conf')).chmod(0o600)
        new=old.replace(b'-/etc/greenmind/archive-export-20261010',b'/etc/greenmind/archive-export-20261010')
        p.write_bytes(new);p.chmod(0o644)
    unit=Path('/etc/systemd/system/greenmind-archive-export-20261010.service');old=unit.read_bytes()
    assert old.count(b'/var/lib/greenmind-archive-coordination')==1
    (ROOT/'export-unit-before.conf').write_bytes(old);(ROOT/'export-unit-before.conf').chmod(0o600)
    unit.write_bytes(old.replace(b'/var/lib/greenmind-archive-coordination',b'/var/lib/greenmind/archive-coordination'));unit.chmod(0o644)
    assert Path('/var/lib/greenmind/archive-coordination').is_dir()
    subprocess.run(['systemd-analyze','verify',*UNITS,'greenmind-archive-export-20261010.service'],check=True,capture_output=True,timeout=15)
    subprocess.run(['systemctl','daemon-reload'],check=True,timeout=15)
    subprocess.run(['systemctl','restart',UNITS[1]],check=True,timeout=25)
    deadline=time.monotonic()+20
    while True:
        try:
            with urlopen('http://127.0.0.1:8141/health',timeout=3)as r:assert json.loads(r.read(8192))['release_id']=='archive-reader-20261010-v48'
            result=actual_masks();break
        except (OSError,AssertionError):
            assert time.monotonic()<deadline,'Private namespace acceptance failed'
            time.sleep(1)
    result={'actual_namespace_credentials_denied':result,'export_coordination_path_corrected':True,'restarted_only_private_broker':True,'public_reader_changed':False,'writer_started':False,'deleted_files':0}
    with (ROOT/'accepted.json').open('x')as out:os.fchmod(out.fileno(),0o600);json.dump(result,out)
    print(json.dumps(result))
if __name__=='__main__':main()
