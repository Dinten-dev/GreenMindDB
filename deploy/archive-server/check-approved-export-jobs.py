"""Two explicitly approved WAVs: actual jobs, ownership, progress and ZIP bytes.

Existing worker only; no source writes, identity changes or cleanup.
"""
import argparse
import base64
import hashlib
import hmac
import io
import json
import os
import time
import wave
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path('/mnt/HC_Volume_106755700/greenmind-delete-preparation/reader-20261010-v22')
BASE = 'http://127.0.0.1:8141/api/v1/visualization/archive-exports'
PHASE = 'preflight'
JOBS = []


def main():
    global PHASE
    assert os.geteuid() == 0 and os.uname().nodename == 'Biolingo'
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resume',type=Path)
    args=parser.parse_args()
    previous={}
    if args.resume:
        p=args.resume
        assert p.parent==ROOT and not p.is_symlink() and p.stat().st_uid==0
        assert not p.stat().st_mode&0o077 and p.stat().st_size<65536
        previous={job['kind']:job for job in json.loads(p.read_text())['jobs']}
        assert set(previous)=={'gateway','direct'}
    settings = dict(s.split('=', 1) for s in (ROOT/'read.env').read_text().splitlines()
                    if s and not s.startswith('#'))

    def engine(key):
        return create_engine(make_url(settings[key]).set(host='127.0.0.1', port=5432),
            pool_size=1, max_overflow=0, connect_args={'connect_timeout': 5,
            'options': '-c default_transaction_read_only=on -c statement_timeout=8000 -c lock_timeout=150'})

    def guard():
        deadline=time.monotonic()+30
        while True:
            memory=int(next(s.split()[1] for s in Path('/proc/meminfo').read_text().splitlines()
                            if s.startswith('MemAvailable:')))
            if memory>=640*1024 and os.getloadavg()[0]<=2.4:
                return
            assert time.monotonic()<deadline, 'source_headroom'
            time.sleep(2)  # No SQL connection is opened while preflight waits.

    def request(url, actor, method='GET'):
        guard()
        encode = lambda value: base64.urlsafe_b64encode(value).rstrip(b'=')
        payload = encode(b'{"alg":"HS256","typ":"JWT"}')+b'.'+encode(
            json.dumps({'sub': str(actor), 'exp': int(time.time())+300}).encode())
        token = (payload+b'.'+encode(hmac.digest(settings['JWT_SECRET_KEY'].encode(), payload, 'sha256'))).decode()
        try:
            with urlopen(Request(url, method=method, headers={'Cookie': 'access_token='+token}), timeout=20) as r:
                body = r.read(2*1024**2+1)
                assert len(body) <= 2*1024**2
                return r.status, body
        except HTTPError as e:
            return e.code, e.read(4096)

    guard()
    legacy = engine('DATABASE_URL')
    direct = engine('DIRECT_DATABASE_URL')
    PHASE = 'gateway_exact_lookup'
    with legacy.connect() as db:
        g = db.execute(text('''SELECT w.sensor_id,w.content_sha256,w.file_size_bytes,w.started_at,w.ended_at,
            z.organization_id,z.id AS zone FROM wav_file w JOIN sensor s ON s.id=w.sensor_id
            JOIN gateway g ON g.id=s.gateway_id JOIN zone z ON z.id=g.zone_id
            WHERE w.id='07f26f01-07fa-4af0-b55e-6bffa641e6a0' ''')).mappings().one()
        middle = g['started_at']+(g['ended_at']-g['started_at'])/2
        bounds = {'start': middle, 'end': middle+timedelta(microseconds=10)}
        PHASE = 'gateway_bounded_overlap'
        assert len(db.execute(text('''SELECT id FROM wav_file WHERE sensor_id=:sensor
            AND started_at<:end AND ended_at>:start LIMIT 2'''), bounds|{'sensor':g['sensor_id']}).all()) == 1
    legacy.dispose()  # Return the narrow role's connection before opening Direct.
    PHASE = 'direct_exact_lookup'
    with direct.connect() as db:
        d = db.execute(text('''SELECT d.id AS device,d.organization_id,d.zone_id AS zone,r.manifest
            FROM direct_device d JOIN direct_segment s ON s.device_id=d.id
            JOIN direct_revision r ON r.segment_id=s.id AND r.revision=s.published_revision
            WHERE s.id='daa0ae4e-2afe-4976-8975-bb7fd4024593' AND s.sealed AND r.verified_at IS NOT NULL''')).mappings().one()
        digest = '03286f6f51635170f6dc5f1f7c431b419e0c1cf0f7784b129d651168ce7d0407'
        run = next(r for r in d['manifest']['runs'] if r['sha256']==digest)
        stamp = datetime.fromtimestamp(run['started_at_us']/1e6, UTC)
        duration = run['frame_count']/d['manifest']['config']['sample_rate']
        dstart = stamp+timedelta(seconds=duration/2)
        dend = dstart+timedelta(microseconds=10)
        assert sum(datetime.fromtimestamp(r['started_at_us']/1e6, UTC)<dend
                   and datetime.fromtimestamp(r['started_at_us']/1e6+r['frame_count']/d['manifest']['config']['sample_rate'], UTC)>dstart
                   for r in d['manifest']['runs']) == 1
    direct.dispose()
    selections = [('gateway',g['sensor_id'],g['organization_id'],g['zone'],middle,
                   middle+timedelta(microseconds=10),g['content_sha256'],g['file_size_bytes']),
                  ('direct',d['device'],d['organization_id'],d['zone'],dstart,dend,digest,456044)]
    actors = []
    PHASE = 'real_actors'
    with legacy.connect() as db:
        for selection in selections:
            org, zone = selection[2:4]
            actor = db.execute(text('''SELECT u.id FROM users u WHERE u.is_active AND u.is_verified
                AND u.organization_id=:org AND (u.role::text IN ('owner','admin') OR EXISTS
                (SELECT 1 FROM zone_access a WHERE a.user_id=u.id AND a.zone_id=:zone)) LIMIT 1'''),
                {'org':org,'zone':zone}).scalar_one()
            other = db.execute(text('SELECT id FROM users WHERE id<>:actor AND is_active AND is_verified LIMIT 1'),
                               {'actor':actor}).scalar_one()
            actors.append((actor,other))
    legacy.dispose()
    for selection,(actor,other) in zip(selections,actors,strict=True):
        kind,sensor,_,_,start,end,sha,size = selection
        PHASE = kind+'_create'
        if previous:
            import uuid
            job=str(uuid.UUID(previous[kind]['id']))
        else:
            status,body = request(BASE+'?'+urlencode({'kind':kind,'sensor_id':str(sensor),
                'from_dt':start.isoformat(),'to_dt':end.isoformat()}),actor,'POST')
            assert status==202
            created=json.loads(body)
            assert created['files']==1 and created['bytes']==size
            job=created['id']
        JOBS.append({'id':job,'kind':kind,'status':'queued'})
        assert request(BASE+'/'+job,other)[0]==404
        PHASE=kind+'_progress'
        deadline=time.monotonic()+100
        progress=[]
        while True:
            status,body=request(BASE+'/'+job,actor)
            assert status==200
            state=json.loads(body); progress.append(state['completed'])
            JOBS[-1]['status']=state['status']
            assert state['total']==1 and state['completed'] in (0,1)
            assert state['status']!='failed'
            if state['status']=='ready': break
            assert time.monotonic()<deadline
            time.sleep(3)
        assert progress==sorted(progress) and state['completed']==1 and state['parts']==1
        PHASE=kind+'_zip'
        assert request(BASE+'/'+job+'/parts/0',other)[0]==404
        status,body=request(BASE+'/'+job+'/parts/0',actor); assert status==200
        with zipfile.ZipFile(io.BytesIO(body)) as bundle:
            manifest=json.loads(bundle.read('manifest.json')); assert len(manifest)==1
            item=manifest[0]; assert item['sha256']==sha and item['size']==size
            assert 'key' not in item and 'bucket' not in item
            assert len(bundle.infolist())==2
            audio=bundle.read(item['name']); assert len(audio)==size and hashlib.sha256(audio).hexdigest()==sha
            with wave.open(io.BytesIO(audio),'rb') as wav:
                assert wav.getnframes()>0
                assert len(wav.readframes(wav.getnframes()))==wav.getnframes()*wav.getnchannels()*wav.getsampwidth()
        JOBS[-1].update(ownership_checked=True,zip_sha_wav_checked=True,progress_checked=True)
    return {'status':'approved_jobs_passed','jobs':JOBS,'existing_worker_only':True,
            'public_routes_changed':False,'deleted_files':0,'reader_accepted':False}


if __name__=='__main__':
    try:
        result=main()
    except Exception as error:  # noqa: BLE001 - never expose credential-bearing driver errors
        result={'status':'blocked','phase':PHASE,'error_type':type(error).__name__,'jobs':JOBS,'deleted_files':0}
        original=getattr(error,'orig',None)
        state=getattr(original,'pgcode',None) or getattr(original,'sqlstate',None)
        if state and len(state)==5 and state.isalnum():
            result['sqlstate']=state
        for phrase in ('permission denied', 'password authentication failed', 'connection refused',
                       'does not exist', 'too many clients', 'too many connections',
                       'statement timeout', 'translate host name'):
            if phrase in str(original if original is not None else error).lower():
                result['database_reason']=phrase
    result['at']=datetime.now(UTC).isoformat()
    with (ROOT/('approved-jobs-'+datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f')+'.json')).open('x') as body:
        os.fchmod(body.fileno(),0o600); json.dump(result,body)
    print(json.dumps(result))
    raise SystemExit(0 if result['status']=='approved_jobs_passed' else 1)
