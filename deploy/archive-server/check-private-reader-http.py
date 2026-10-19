"""Actual bounded HTTP acceptance outside the memory-limited candidate container."""

import base64
import csv
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
BASE = 'http://127.0.0.1:8141'
PHASE = 'preflight'
CHECKS = {}


def headroom():
    memory = int(next(s.split()[1] for s in Path('/proc/meminfo').read_text().splitlines()
                      if s.startswith('MemAvailable:')))
    assert memory >= 640 * 1024 and os.getloadavg()[0] <= 2.4, 'source_headroom'


def main():
    global PHASE
    assert os.geteuid() == 0 and os.uname().nodename == 'Biolingo'
    headroom()
    settings = dict(s.split('=', 1) for s in (ROOT/'read.env').read_text().splitlines()
                    if s and not s.startswith('#'))
    dburl = make_url(settings['DATABASE_URL']).set(host='127.0.0.1', port=5432)
    engine = create_engine(dburl, pool_size=1, max_overflow=0, connect_args={
        'connect_timeout': 5, 'options': '-c default_transaction_read_only=on -c statement_timeout=8000 -c lock_timeout=150'})
    with engine.connect() as db:
        row = db.execute(text("""SELECT u.id AS actor,w.id AS wav,w.sensor_id,
          w.content_sha256,w.file_size_bytes,w.started_at,w.ended_at
          FROM users u JOIN zone z ON z.organization_id=u.organization_id
          JOIN gateway g ON g.zone_id=z.id JOIN sensor s ON s.gateway_id=g.id
          JOIN wav_file w ON w.sensor_id=s.id
          WHERE u.is_active AND u.is_verified AND w.feature_status='verified'
          AND w.file_size_bytes BETWEEN 1000 AND 1048576
          AND (u.role::text IN ('owner','admin') OR EXISTS
               (SELECT 1 FROM zone_access a WHERE a.user_id=u.id AND a.zone_id=z.id))
          ORDER BY w.started_at DESC LIMIT 1""")).mappings().one()
        other = db.execute(text("""SELECT u.id FROM users u WHERE u.id<>:actor
          AND u.is_active AND u.is_verified AND NOT EXISTS
          (SELECT 1 FROM sensor s JOIN gateway g ON g.id=s.gateway_id JOIN zone z ON z.id=g.zone_id
          WHERE s.id=:sensor AND z.organization_id=u.organization_id
          AND (u.role::text IN ('owner','admin') OR EXISTS
               (SELECT 1 FROM zone_access a WHERE a.user_id=u.id AND a.zone_id=z.id))) LIMIT 1"""),
          {'actor': row['actor'], 'sensor': row['sensor_id']}).scalar_one()
    engine.dispose()

    # This exact Direct recording was separately approved for full readback.
    device = '217bb811-57c3-4792-bde8-0a06f672654a'
    segment = 'daa0ae4e-2afe-4976-8975-bb7fd4024593'
    direct_engine = create_engine(
        make_url(settings['DIRECT_DATABASE_URL']).set(host='127.0.0.1', port=5432),
        pool_size=1, max_overflow=0, connect_args={
            'connect_timeout': 5,
            'options': '-c default_transaction_read_only=on -c statement_timeout=8000 -c lock_timeout=150'})
    with direct_engine.connect() as db:
        direct_zone = db.execute(text('SELECT organization_id,zone_id FROM direct_device WHERE id=:id'),
                                 {'id': device}).mappings().one()
        approved_manifest = db.execute(text('SELECT manifest FROM direct_revision WHERE segment_id=:id AND revision=601'),
                                       {'id': segment}).scalar_one()
        approved_run = next(i for i, run in enumerate(approved_manifest['runs'])
                            if run['sha256'] == '03286f6f51635170f6dc5f1f7c431b419e0c1cf0f7784b129d651168ce7d0407')
    direct_engine.dispose()
    with engine.connect() as db:
        direct_actor = db.execute(text("""SELECT u.id FROM users u WHERE u.is_active
          AND u.is_verified AND u.organization_id=:org AND
          (u.role::text IN ('owner','admin') OR EXISTS
          (SELECT 1 FROM zone_access a WHERE a.user_id=u.id AND a.zone_id=:zone)) LIMIT 1"""),
          {'org': direct_zone['organization_id'], 'zone': direct_zone['zone_id']}).scalar_one()
        direct_other = db.execute(text("""SELECT u.id FROM users u WHERE u.is_active
          AND u.is_verified AND (u.organization_id IS DISTINCT FROM :org OR
          (u.role::text NOT IN ('owner','admin') AND NOT EXISTS
          (SELECT 1 FROM zone_access a WHERE a.user_id=u.id AND a.zone_id=:zone))) LIMIT 1"""),
          {'org': direct_zone['organization_id'], 'zone': direct_zone['zone_id']}).scalar_one()
    engine.dispose()

    def token(actor):
        def encoded(value):
            return base64.urlsafe_b64encode(value).rstrip(b'=')
        payload = encoded(b'{"alg":"HS256","typ":"JWT"}') + b'.' + encoded(
            json.dumps({'sub': str(actor), 'exp': int(time.time())+300}).encode())
        return (payload+b'.'+encoded(hmac.digest(settings['JWT_SECRET_KEY'].encode(), payload, 'sha256'))).decode()

    def request(path, actor=row['actor']):
        headroom()
        headers = {} if actor is None else {'Cookie': 'access_token='+token(actor)}
        req = Request(BASE+path, headers=headers)
        try:
            with urlopen(req, timeout=20) as response:
                data = response.read(4*1024**2+1)
                assert len(data) <= 4*1024**2
                return response.status, data
        except HTTPError as error:
            return error.code, error.read(4096)

    checks = CHECKS
    sensor = '/api/v1/sensors/'+str(row['sensor_id'])
    checks['anonymous_401'] = request(sensor+'/data', None)[0] == 401
    checks['real_zone_denial_404'] = request(sensor+'/data', other)[0] == 404
    checks['missing_sensor_404'] = request('/api/v1/sensors/00000000-0000-0000-0000-000000000001/data')[0] == 404
    end = datetime.now(UTC)
    counts = {}
    for label, duration in [('1h', timedelta(hours=1)), ('7d', timedelta(days=7)),
                            ('30d', timedelta(days=30)), ('1y', timedelta(days=365))]:
        PHASE = 'gateway_series_' + label
        query = urlencode({'from_dt': (end-duration).isoformat(), 'to_dt': end.isoformat()})
        status, data = request(sensor+'/data?'+query)
        assert status == 200
        records = json.loads(data)
        counts[label] = sum(len(r['data']) for r in records)
        assert counts[label] > 0
        if label == '1h':
            PHASE = 'gateway_csv_display'
            status, data = request(sensor+'/export?'+query)
            assert status == 200
            with zipfile.ZipFile(io.BytesIO(data)) as bundle:
                exported = []
                for name in bundle.namelist():
                    contents = bundle.read(name).decode()
                    exported.extend(csv.DictReader(s for s in contents.splitlines() if not s.startswith('#')))
            points = [p for series in records for p in series['data']]
            assert len(exported) == len(points)
            assert sorted((p['timestamp_utc'], float(p['value'])) for p in exported) == sorted(
                (p['timestamp'], float(p['value'])) for p in points)
            checks['csv_display_agreement'] = True
    PHASE = 'gateway_original_wav'
    status, audio = request('/api/v1/wav/download/'+str(row['wav']))
    assert status == 200 and len(audio) == row['file_size_bytes']
    assert hashlib.sha256(audio).hexdigest() == row['content_sha256']
    with wave.open(io.BytesIO(audio), 'rb') as wav:
        assert wav.getnframes() > 0
        assert len(wav.readframes(wav.getnframes())) == wav.getnframes()*wav.getnchannels()*wav.getsampwidth()
    checks['gateway_original_url_sha_full_wav_decode'] = True
    direct_path = f'/api/v1/visualization/direct/{device}/wav/{segment}/601/{approved_run}'
    PHASE = 'direct_real_authorization'
    checks['direct_anonymous_401'] = request(direct_path, None)[0] == 401
    checks['direct_real_zone_denial_404'] = request(direct_path, direct_other)[0] == 404
    PHASE = 'direct_approved_wav'
    status, audio = request(direct_path, direct_actor)
    assert status == 200 and len(audio) == 456044, 'direct_status_or_size'
    assert hashlib.sha256(audio).hexdigest() == '03286f6f51635170f6dc5f1f7c431b419e0c1cf0f7784b129d651168ce7d0407', 'direct_sha256'
    with wave.open(io.BytesIO(audio), 'rb') as wav:
        assert wav.getnframes() > 0
        assert len(wav.readframes(wav.getnframes())) == wav.getnframes()*wav.getnchannels()*wav.getsampwidth()
    checks['direct_approved_original_url_sha_full_wav_decode'] = True
    PHASE = 'authorization_checks'
    assert all(checks.values())
    return {'status': 'partial_http_passed', 'at': datetime.now(UTC).isoformat(), 'checks': checks,
            'series_points': counts, 'archive_only_fallback_accepted': False,
            'direct_original_url_accepted': True, 'export_jobs_accepted': False,
            'reader_accepted': False, 'public_routes_changed': False, 'deleted_files': 0}


if __name__ == '__main__':
    try:
        result = main()
    except Exception as error:  # noqa: BLE001 - never print credential-bearing driver errors
        result = {'status': 'blocked', 'error_type': type(error).__name__, 'phase': PHASE,
                  'checks': CHECKS, 'deleted_files': 0}
        if isinstance(error, AssertionError) and str(error) in {'source_headroom', 'direct_status_or_size', 'direct_sha256'}:
            result['reason'] = str(error)
    path = ROOT / ('http-check-'+datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f')+'.json')
    with path.open('x') as body:
        os.fchmod(body.fileno(), 0o600)
        json.dump(result, body)
    print(json.dumps(result))
    raise SystemExit(0 if result['status'] == 'partial_http_passed' else 1)
