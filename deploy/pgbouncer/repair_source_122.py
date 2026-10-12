"""Compatibility repair for the new, unused 1.22 service only."""
import hashlib
import json
import os
import subprocess
from pathlib import Path

assert os.geteuid()==0 and os.uname().nodename=='Biolingo'
root=Path('/etc/greenmind/pgbouncer-fallback-20261011')
p=root/'pgbouncer.ini'; raw=p.read_bytes()
unit=Path('/etc/systemd/system/greenmind-pgbouncer-fallback-20261011.service'); oldunit=unit.read_bytes()
assert hashlib.sha256(raw).hexdigest()=='9780cc80848269fd713049e8f52a328e6a8075bfcfd95470a6f9e2e2338c61d9' or (root/'before-1.22-repair.ini').is_file()
subprocess.run(['systemctl','stop','greenmind-pgbouncer-fallback-20261011.service'],check=True,timeout=10)
for name,body in [('before-1.22-repair.ini',raw),('before-1.22-repair.service',oldunit)]:
    if not (root/name).exists():
        with (root/name).open('xb')as out:os.fchmod(out.fileno(),0o600);out.write(body)
new=raw.replace(b'[users]\nadmin = pool_size=3 max_user_connections=3\n',b'')
assert b'[users]'not in new and b'max_db_connections = 3' in new and b'max_user_connections = 3' in new
p.write_bytes(new);p.chmod(0o600)
newunit=oldunit if b'StartLimitIntervalSec=60' in oldunit else oldunit.replace(b'Description=GreenMind bounded old admin fallback connection pool\n',b'Description=GreenMind bounded old admin fallback connection pool\nStartLimitIntervalSec=60\nStartLimitBurst=3\n')
unit.write_bytes(newunit);unit.chmod(0o644)
subprocess.run(['systemd-analyze','verify',str(unit)],check=True,capture_output=True,timeout=10)
subprocess.run(['systemctl','daemon-reload'],check=True,timeout=10)
subprocess.run(['systemctl','reset-failed','greenmind-pgbouncer-fallback-20261011.service'],check=False,capture_output=True,timeout=10)
subprocess.run(['systemctl','start','greenmind-pgbouncer-fallback-20261011.service'],check=True,timeout=10)
result={'repaired_122_user_syntax':True,'only_new_unused_pool_changed':True,'backend_maximum':3,'config_sha256':hashlib.sha256(new).hexdigest(),'unit_sha256':hashlib.sha256(newunit).hexdigest(),'deleted_files':0}
with(root/'repaired-122.json').open('x')as out:os.fchmod(out.fileno(),0o600);json.dump(result,out)
print(json.dumps(result))
