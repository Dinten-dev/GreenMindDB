"""Retain v69 and prepare one hash-guarded nonblocking authentication fix."""
import hashlib,json,os,shutil,time
from pathlib import Path
import switch_archive
assert os.geteuid()==0 and os.uname().nodename=='ubuntu-4gb-fsn1-1'
switch_archive.guard()
root=Path('/etc/greenmind/pgbouncer-20261011')
assert not(root/'accepted-v69.json').exists() and not(root/'rehearsed-v69.json').exists()
base=Path('/opt/greenmind/archive-preparation/20261011-pgbouncer-v69')
target=Path('/opt/greenmind/archive-preparation/20261011-pgbouncer-v69-authfix')
assert not target.exists()
raw=(base/'files.json').read_bytes()
assert hashlib.sha256(raw).hexdigest()=='0fa7f907243568112a5a2d356320bafebbb0ee242f0ea03b4004e33387da5a86'
files=json.loads(raw)
for name,sha in files.items():assert hashlib.sha256((base/name).read_bytes()).hexdigest()==sha
name='backend/app/visualization/remote_auth.py'
assert files[name]=='483fcba02901953127b8c7fc8ec3fbc564b0640b33584b5b856ae49b7928a142'
payload=Path('/home/traver/greenmind-pgbouncer-20261011/remote_auth_nonblocking.py').read_bytes()
assert hashlib.sha256(payload).hexdigest()=='581e70f607b62915c8f3e57bef74c3c72dc6289a8c574e127a481cdf84ce37a4'
compile(payload,name,'exec')
oldmanifest=(root/'overlays.json').read_bytes();manifest=json.loads(oldmanifest)
updates=[]
for row in manifest['rows']:
 p=Path(row['env']);old=p.read_bytes()
 assert hashlib.sha256(old).hexdigest()==row['env_sha256']
 source=Path(row['source']);assert hashlib.sha256(source.read_bytes()).hexdigest()==row['source_sha256']
 # Public reader must still be restored to the original environment.
 assert Path(row['override']).read_text()=='[Service]\nEnvironmentFile='+row['source']+'\n'
 oldpath=b'PYTHONPATH='+str(base/'backend').encode()
 assert old.count(oldpath)==1
 updates.append((row,p,old,old.replace(oldpath,b'PYTHONPATH='+str(target/'backend').encode())))
shutil.copytree(base,target)
(target/name).write_bytes(payload)
files[name]=hashlib.sha256(payload).hexdigest()
(target/'files.json').write_text(json.dumps(files,sort_keys=True))
for changed,sha in files.items():assert hashlib.sha256((target/changed).read_bytes()).hexdigest()==sha
with(root/'overlays-before-authfix.json').open('xb')as f:os.fchmod(f.fileno(),0o600);f.write(oldmanifest)
for row,p,old,new in updates:
 with p.with_name(p.name+'.before-authfix').open('xb')as f:os.fchmod(f.fileno(),0o600);f.write(old)
 p.write_bytes(new);p.chmod(0o600);row['env_sha256']=hashlib.sha256(new).hexdigest()
(root/'overlays.json').write_text(json.dumps(manifest));(root/'overlays.json').chmod(0o600)
result={'at':time.time(),'runtime':str(target),'manifest_sha256':hashlib.sha256((target/'files.json').read_bytes()).hexdigest(),'additional_changed_source_files':[name],'original_v69_retained':True,'public_reader_unchanged':True,'receivers_unchanged':True,'deleted_files':0}
with(root/'authfix-prepared.json').open('x')as f:os.fchmod(f.fileno(),0o600);json.dump(result,f)
print(json.dumps(result))
