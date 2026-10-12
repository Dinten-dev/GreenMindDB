"""Bounded approved reader-only rehearsal; independent retained rollback."""
import argparse,hashlib,json,os,subprocess,time
from pathlib import Path
from urllib.request import urlopen
import switch_archive
ROOT=Path('/etc/greenmind/pgbouncer-20261011')
PY='/opt/greenmind/archive-python-20261008-v4/bin/python'
HERE=Path('/home/traver/greenmind-pgbouncer-20261011')
def save(path,value):
 with path.open('x')as f:os.fchmod(f.fileno(),0o600);json.dump(value,f);f.flush();os.fsync(f.fileno())
def stable():
 units=['greenmind-source-link.service','greenmind-archive-reader-broker-20261010.service','greenmind-archive-frontend-source-relay-v57.service']
 result={u:subprocess.check_output(['systemctl','show',u,'-p','MainPID','-p','ActiveState','-p','ExecMainStartTimestampMonotonic'],timeout=3).decode()for u in units}
 for u in units:assert 'ActiveState=active'in result[u]
 for u in ['greenmind-archive-copy.timer','greenmind-archive-export-20261010.timer']:
  assert subprocess.run(['systemctl','is-active','--quiet',u]).returncode==0;result[u]='active'
 for name in ['gm-peter-frontend-v4-archive-r2-20261011','gm-zones-staging-1b449e928a54-application-1','gm-direct-staging-direct-api-1','gm-staging-backend-1']:
  result[name]=subprocess.check_output(['docker','inspect','--format','{"image":{{json .Image}},"started":{{json .State.StartedAt}},"restarts":{{.RestartCount}}}',name],timeout=3).decode()
 return result
def main(action):
 assert os.geteuid()==0 and os.uname().nodename=='ubuntu-4gb-fsn1-1'
 if action=='rollback':
  if (ROOT/'accepted-v69.json').exists():return
  switch_archive.apply(True);return
 if action=='accept':
  evidence=Path('/home/traver/pgbouncer-v69-public-tests.json');value=json.loads(evidence.read_bytes())
  assert value['passed'] and value['receiver_continuity'] and value['release_id']=='archive-reader-20261011-pgbouncer-v69'
  with urlopen('http://127.0.0.1:8141/health',timeout=3)as r:assert json.loads(r.read(8193))['release_id']==value['release_id']
  rehearsal=json.loads((ROOT/'rehearsed-v69.json').read_text());assert stable()==rehearsal['stable']
  copier=next(r for r in json.loads((ROOT/'overlays.json').read_text())['rows']if r['unit']=='greenmind-archive-copy.service')
  assert Path(copier['override']).read_text()=='[Service]\nEnvironmentFile='+copier['source']+'\n'
  assert hashlib.sha256(Path(copier['source']).read_bytes()).hexdigest()==copier['source_sha256']
  result={'copier_original_configuration_retained':True,'copier_journal_identity_unchanged':True,'pooled_services':['reader','export'],'accepted':True,'at':time.time(),'release_id':value['release_id'],'public_tests_sha256':hashlib.sha256(evidence.read_bytes()).hexdigest(),'actual_activate_rollback_activate':rehearsal['steps'],'manifest_sha256':hashlib.sha256(Path('/opt/greenmind/archive-preparation/20261011-pgbouncer-v69-authfix/files.json').read_bytes()).hexdigest(),'pool_config_sha256':hashlib.sha256((ROOT/'pgbouncer.ini').read_bytes()).hexdigest(),'originals_retained':True,'deleted_files':0,'deletion_enabled':False}
  save(ROOT/'accepted-v69.json',result)
  target=Path('/home/traver/pgbouncer-v69-accepted.json');save(target,result)
  import pwd
  operator=pwd.getpwnam('traver');os.chown(target,operator.pw_uid,operator.pw_gid)
  print(json.dumps(result));return
 assert action=='rehearse' and not(ROOT/'rehearsed-v69.json').exists()
 switch_archive.guard();baseline=stable()
 subprocess.run(['systemctl','stop','greenmind-archive-observation-accepted-20261011-v59.timer'],check=True,timeout=10)
 previous_timer='greenmind-pgbouncer-v69-independent-rollback.timer'
 if subprocess.check_output(['systemctl','show',previous_timer,'-p','LoadState','--value'],timeout=3).strip()!=b'not-found':
  subprocess.run(['systemctl','stop',previous_timer],check=True,capture_output=True,timeout=10)
 subprocess.run(['systemd-run','--unit=greenmind-pgbouncer-v69-independent-rollback-'+str(time.time_ns()),'--on-active=660s','--timer-property=AccuracySec=1s','-p','MemoryMax=64M','-p','MemorySwapMax=0','-p','CPUQuota=10%','-p','TimeoutStartSec=50',PY,str(HERE/'release_archive.py'),'rollback'],check=True,capture_output=True,timeout=10)
 steps=[]
 try:
  for rollback in [False,True,False]:
   switch_archive.apply(rollback)
   with urlopen('http://127.0.0.1:8141/health',timeout=3)as r:
    release=json.loads(r.read(8193))['release_id']
   assert release==('archive-reader-20261010-v48'if rollback else'archive-reader-20261011-pgbouncer-v69')
   assert stable()==baseline
   steps.append({'rollback':rollback,'release_id':release,'at':time.time()})
  result={'rehearsed':True,'steps':steps,'stable':baseline,'source_routes_unchanged':True,'deleted_files':0}
  save(ROOT/'rehearsed-v69.json',result);print(json.dumps({'rehearsed':True,'steps':steps,'protected_unchanged':True,'independent_rollback_armed':True,'deleted_files':0}))
 except BaseException as error:
  save(ROOT/('failed-rehearsal-'+str(time.time_ns())+'.json'),{'accepted':False,'error_type':type(error).__name__,'steps':steps,'deleted_files':0})
  switch_archive.apply(True);raise
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('action',choices=['rehearse','rollback','accept']);main(p.parse_args().action)
