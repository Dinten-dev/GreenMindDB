"""Immutable v48-based candidate with transaction-local safeguards only."""
import hashlib,json,os,shutil,subprocess
from pathlib import Path
from urllib.request import urlopen
assert os.geteuid()==0 and os.uname().nodename=='ubuntu-4gb-fsn1-1'
available=int(next(x.split()[1]for x in Path('/proc/meminfo').read_text().splitlines()if x.startswith('MemAvailable:')))/1024
assert available>=864 and os.getloadavg()[0]<=2
for port in (18120,18003,18000):
    with urlopen(f'http://127.0.0.1:{port}/health',timeout=3)as response:assert response.status==200
assert subprocess.run(['systemctl','is-active','--quiet','greenmind-pgbouncer-20261011.service']).returncode!=0
base=Path('/opt/greenmind/archive-preparation/20261010-v48')
target=Path('/opt/greenmind/archive-preparation/20261011-pgbouncer-v69')
root=Path('/etc/greenmind/pgbouncer-20261011')
assert not target.exists()
files=json.loads((base/'files.json').read_text())
for name,sha in files.items():assert hashlib.sha256((base/name).read_bytes()).hexdigest()==sha
shutil.copytree(base,target)
helper=Path('/home/traver/greenmind-pgbouncer-20261011/transaction_pool.py').read_bytes()
compile(helper,'transaction_pool.py','exec')
(target/'backend/app/transaction_pool.py').write_bytes(helper)
changes={
'backend/app/visualization/api.py':[(b'from app.zone_access import zone_access_filter, is_platform_admin',b'from app.zone_access import zone_access_filter, is_platform_admin\nfrom app.transaction_pool import attach_read_guards'),(b'\n\ndef read_db():',b'\nattach_read_guards(read_engine, statement_ms=8000, lock_ms=150, work_mem_kib=8192)\n\n\ndef read_db():')],
'backend/app/visualization/direct.py':[(b'from app.direct.storage import ArtifactStore',b'from app.direct.storage import ArtifactStore\nfrom app.transaction_pool import attach_read_guards'),(b'    return engine, ArtifactStore(cfg)',b'    attach_read_guards(engine, statement_ms=8000, lock_ms=150, work_mem_kib=8192)\n    return engine, ArtifactStore(cfg)')],
'backend/app/raw_archive/runner.py':[(b'    from sqlalchemy import create_engine',b'    from sqlalchemy import create_engine\n    from app.transaction_pool import attach_read_guards'),(b'        cleanup.callback(engine.dispose)',b'        attach_read_guards(engine, statement_ms=3000, lock_ms=500, idle_ms=5000)\n        cleanup.callback(engine.dispose)')]
}
for name,replacements in changes.items():
    p=target/name;raw=p.read_bytes()
    for old,new in replacements:assert raw.count(old)==1;raw=raw.replace(old,new)
    compile(raw,name,'exec');p.write_bytes(raw)
updated={name:hashlib.sha256((target/name).read_bytes()).hexdigest()for name in files}
updated['backend/app/transaction_pool.py']=hashlib.sha256(helper).hexdigest()
(target/'files.json').write_text(json.dumps(updated,sort_keys=True))
oldmanifest=(root/'overlays.json').read_bytes();manifest=json.loads(oldmanifest)
with(root/'overlays-before-local-guards.json').open('xb')as out:os.fchmod(out.fileno(),0o600);out.write(oldmanifest)
for row in manifest['rows']:
    p=Path(row['env']);raw=p.read_bytes();assert hashlib.sha256(raw).hexdigest()==row['env_sha256']
    with p.with_name(p.name+'.before-local-guards').open('xb')as out:os.fchmod(out.fileno(),0o600);out.write(raw)
    values=dict(l.split('=',1)for l in raw.decode().splitlines()if l)
    values.update(GREENMIND_TRANSACTION_POOL_READS='true',PYTHONPATH=str(target/'backend'))
    if p.name!='copy.env':values['RELEASE_ID']='archive-reader-20261011-pgbouncer-v69'
    new=''.join(k+'='+v+'\n'for k,v in values.items()).encode();p.write_bytes(new);p.chmod(0o600)
    row['env_sha256']=hashlib.sha256(new).hexdigest()
(root/'overlays.json').write_text(json.dumps(manifest));(root/'overlays.json').chmod(0o600)
config=root/'pgbouncer.ini';raw=config.read_bytes();assert hashlib.sha256(raw).hexdigest()=='db4a54929b153ea057ff810ebe01d7e60ee1e999b41f990fa5a21b19a122ca05'
with(root/'before-no-new-idle-limit.ini').open('xb')as out:os.fchmod(out.fileno(),0o600);out.write(raw)
config.write_bytes(raw.replace(b'idle_transaction_timeout = 10',b'idle_transaction_timeout = 0'));config.chmod(0o600)
subprocess.run(['systemctl','restart','greenmind-pgbouncer-20261011.service'],check=True,timeout=10)
result={'immutable_candidate_prepared':True,'base_runtime':'20261010-v48','candidate_runtime':target.name,'changed_source_files':list(changes)+['backend/app/transaction_pool.py'],'source_files_unchanged':len(files)-3,'startup_guards_enforced_transaction_locally':True,'existing_api_idle_policy_preserved':True,'manifest_sha256':hashlib.sha256((target/'files.json').read_bytes()).hexdigest(),'config_sha256':hashlib.sha256(config.read_bytes()).hexdigest(),'deleted_files':0}
with(root/'v69-prepared.json').open('x')as out:os.fchmod(out.fileno(),0o600);json.dump(result,out)
print(json.dumps(result))
