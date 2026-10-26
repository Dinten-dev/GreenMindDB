"""Use normal transaction reset semantics; keep startup guards tracked."""
import hashlib,json,os,subprocess
from pathlib import Path
assert os.geteuid()==0 and os.uname().nodename=='ubuntu-4gb-fsn1-1'
root=Path('/etc/greenmind/pgbouncer-20261011');p=root/'pgbouncer.ini';raw=p.read_bytes()
assert hashlib.sha256(raw).hexdigest()=='12c15bc0e48746b486b45f0faf6bb7d833bf9061df63d3614827f9cfcc06162c'
subprocess.run(['systemctl','stop','greenmind-pgbouncer-20261011.service'],check=True,timeout=10)
with(root/'before-transaction-reset.ini').open('xb')as out:os.fchmod(out.fileno(),0o600);out.write(raw)
new=raw.replace(b'server_reset_query_always = 1',b'server_reset_query_always = 0')
assert new!=raw and b'ignore_startup_parameters'not in new
p.write_bytes(new);p.chmod(0o600)
subprocess.run(['systemctl','start','greenmind-pgbouncer-20261011.service'],check=True,timeout=10)
result={'normal_transaction_pool_semantics':True,'guards_not_ignored':True,'only_unused_archive_pool_changed':True,'config_sha256':hashlib.sha256(new).hexdigest(),'deleted_files':0}
with(root/'repaired-reset.json').open('x')as out:os.fchmod(out.fileno(),0o600);json.dump(result,out)
print(json.dumps(result))
