"""Run existing metadata pool checks only while holding the existing source lease."""
import json, os, subprocess, sys
from pathlib import Path
from app.raw_archive.remote_lease import catalog_lease
from app.raw_archive.health import SafetyPause
assert os.geteuid()==0 and os.uname().nodename=='ubuntu-4gb-fsn1-1'
values=dict(line.split('=',1)for line in Path('/etc/greenmind/archive-copy-20261010/copy.env').read_text().splitlines()if line and not line.startswith('#'))
for key,value in values.items():
 if key.startswith('RAW_ARCHIVE_LEASE_'):os.environ[key]=value
os.environ['RAW_ARCHIVE_WORKER_ROLE']='archive'
try:
 with catalog_lease()as check:
  check()
  result=subprocess.run([sys.executable,str(Path(__file__).with_name('check.py')),*sys.argv[1:]],timeout=20)
  check()
 raise SystemExit(result.returncode)
except SafetyPause as error:
 print(json.dumps({'passed':False,'paused':True,'reason':error.code,'deleted_files':0}))
 raise SystemExit(75)
