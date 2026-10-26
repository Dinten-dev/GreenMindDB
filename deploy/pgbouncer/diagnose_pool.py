"""Read bounded PgBouncer statistics; never publish credentials or SQL contents."""
import json,os,time
from pathlib import Path
import psycopg2
assert os.geteuid()==0 and os.uname().nodename=='ubuntu-4gb-fsn1-1'
root=Path('/etc/greenmind/pgbouncer-20261011')
db=psycopg2.connect(host='127.0.0.1',port=6432,dbname='pgbouncer',user='greenmind_pool_stats',password=(root/'stats-secret').read_text(),connect_timeout=3)
try:
 db.autocommit=True
 with db.cursor()as c:
  result={'at':time.time(),'read_only':True,'deleted_files':0}
  for sql,keep in [('SHOW POOLS',('database','user','cl_active','cl_waiting','sv_active','sv_idle','sv_used','sv_tested','sv_login','pool_mode')),('SHOW SERVERS',('database','user','state','remote_pid')),('SHOW USERS',('name','current_connections','max_user_connections','pool_size'))]:
   c.execute(sql);columns=[v[0]for v in c.description]
   result[sql]=[{k:value for k,value in zip(columns,row)if k in keep}for row in c.fetchall()]
finally:db.close()
print(json.dumps(result))
