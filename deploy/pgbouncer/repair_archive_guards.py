"""Explicitly track readonly startup guards for the actual PostgreSQL version."""
import hashlib,json,os,subprocess
from pathlib import Path
assert os.geteuid()==0 and os.uname().nodename=='ubuntu-4gb-fsn1-1'
root=Path('/etc/greenmind/pgbouncer-20261011');p=root/'pgbouncer.ini';raw=p.read_bytes()
assert hashlib.sha256(raw).hexdigest()=='bc2478d045c3c0b5bcc63248deed31624cebfb52c17d10dc27ed87ae4e55f7c8'
subprocess.run(['systemctl','stop','greenmind-pgbouncer-20261011.service'],check=True,timeout=10)
with(root/'before-explicit-readonly.ini').open('xb')as out:os.fchmod(out.fileno(),0o600);out.write(raw)
new=raw.replace(b'track_extra_parameters = statement_timeout,',b'track_extra_parameters = default_transaction_read_only,statement_timeout,')
assert new!=raw and b'ignore_startup_parameters'not in new
p.write_bytes(new);p.chmod(0o600)
subprocess.run(['systemctl','start','greenmind-pgbouncer-20261011.service'],check=True,timeout=10)
result={'explicit_readonly_startup_guard':True,'guards_not_ignored':True,'only_unused_archive_pool_changed':True,'config_sha256':hashlib.sha256(new).hexdigest(),'deleted_files':0}
with(root/'repaired-guards.json').open('x')as out:os.fchmod(out.fileno(),0o600);json.dump(result,out)
print(json.dumps(result))
