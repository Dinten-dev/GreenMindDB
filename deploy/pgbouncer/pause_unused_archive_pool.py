"""Stop only the unused private candidate pool while exact-code approval is pending."""
import json,os,subprocess,time
from pathlib import Path
assert os.geteuid()==0 and os.uname().nodename=='ubuntu-4gb-fsn1-1'
root=Path('/etc/greenmind/pgbouncer-20261011')
manifest=json.loads((root/'overlays.json').read_text())
assert all(not Path(row['override']).exists() for row in manifest['rows'])
assert subprocess.run(['systemctl','is-active','--quiet','greenmind-pgbouncer-reader-candidate-v68.service']).returncode!=0
subprocess.run(['systemctl','stop','greenmind-pgbouncer-20261011.service'],check=True,timeout=10)
result={'at':time.time(),'only_unused_archive_pool_stopped':True,'reader_and_copy_original_configuration_preserved':True,'deletion_enabled':False,'deleted_files':0}
with(root/'paused-pending-guard-code-review.json').open('x')as out:os.fchmod(out.fileno(),0o600);json.dump(result,out)
print(json.dumps(result))
