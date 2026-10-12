"""Restore only the copier's original environment; reader/export and schedules stay."""
import hashlib,json,os,subprocess,time
from pathlib import Path
assert os.geteuid()==0 and os.uname().nodename=='ubuntu-4gb-fsn1-1'
root=Path('/etc/greenmind/pgbouncer-20261011')
row=next(r for r in json.loads((root/'overlays.json').read_text())['rows']if r['unit']=='greenmind-archive-copy.service')
source=Path(row['source']);assert hashlib.sha256(source.read_bytes()).hexdigest()==row['source_sha256']
p=Path(row['override']);old=p.read_text();original='[Service]\nEnvironmentFile='+row['source']+'\n'
assert old in (row['content'],original)
p.write_text(original);p.chmod(0o644)
subprocess.run(['systemctl','daemon-reload'],check=True,timeout=10)
result={'at':time.time(),'copier_original_environment_restored':True,'no_service_restarted':True,'reader_export_schedules_unchanged':True,'journal_unchanged':True,'deleted_files':0}
with(root/('copy-only-rollback-'+str(time.time_ns())+'.json')).open('x')as f:os.fchmod(f.fileno(),0o600);json.dump(result,f)
print(json.dumps(result))
