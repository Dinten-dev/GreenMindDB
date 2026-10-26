"""Private two-object S3-miss rehearsal; originals and public routes stay intact.

The TLS fixture never forwards requests or touches MinIO. Only the unpublished
candidate uses it. An independent restore timer is armed before configuration
changes. Success proves actual authorized HTTP -> read-only broker -> Storage Box.
"""

import argparse
import base64
import hashlib
import hmac
import io
import json
import os
import ssl
import subprocess
import threading
import time
import wave
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import unquote, urlsplit
from urllib.request import Request, urlopen

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path('/mnt/HC_Volume_106755700/greenmind-delete-preparation/reader-20261010-v22')
BASE = 'http://127.0.0.1:8141'
WAV = '07f26f01-07fa-4af0-b55e-6bffa641e6a0'
DEVICE = '217bb811-57c3-4792-bde8-0a06f672654a'
SEGMENT = 'daa0ae4e-2afe-4976-8975-bb7fd4024593'
EXPECTED = {
    'gateway': ('6bf907bbbe43231db74d095c6ee9f0782bca3a3430bb2d2db64ccee25bdcdab9', 456084),
    'direct': ('03286f6f51635170f6dc5f1f7c431b419e0c1cf0f7784b129d651168ce7d0407', 456044),
}
TIMER = 'greenmind-private-reader-fault-restore-20261010'


def command(args, timeout=20):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=timeout)


def reserve(minimum=640):
    memory = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines()
                      if line.startswith('MemAvailable:'))) / 1024
    return memory >= minimum and os.getloadavg()[0] <= 2.4


def wait_reserve(minimum, seconds):
    end = time.monotonic()+seconds
    while not reserve(minimum):
        if time.monotonic() >= end:
            raise RuntimeError('source_headroom')
        time.sleep(2)  # No SQL is opened while waiting.


def environment():
    path = ROOT/'read.env'
    assert path.stat().st_uid == 0 and not path.stat().st_mode & 0o007
    return dict(line.split('=', 1) for line in path.read_text().splitlines()
                if line and not line.startswith('#'))


def restored(folder):
    info = json.loads((folder/'restore.json').read_text())
    original = folder/'before.env'
    assert original.stat().st_uid == 0 and not original.stat().st_mode & 0o077
    assert hashlib.sha256(original.read_bytes()).hexdigest() == info['sha256']
    current = ROOT/'read.env'
    digest = hashlib.sha256(current.read_bytes()).hexdigest()
    assert digest in {info['sha256'], info['fault_sha256']}, 'concurrent_private_config_change'
    if digest == info['fault_sha256']:
        command(['docker', 'compose', '-f', str(ROOT/'compose.json'), 'stop', 'api'], 30)
        with current.open('wb') as body:
            body.write(original.read_bytes()); body.flush(); os.fsync(body.fileno())
    try:
        with urlopen(BASE+'/health', timeout=2) as response:
            running = response.status == 200
    except OSError:
        running = False
    if not running or digest == info['fault_sha256']:
        wait_reserve(896, 90)
        command(['docker', 'compose', '-f', str(ROOT/'compose.json'), 'up', '-d', '--no-build', '--no-deps', 'api'], 35)
    return hashlib.sha256(current.read_bytes()).hexdigest() == info['sha256']


def engine(settings, key):
    return create_engine(make_url(settings[key]).set(host='127.0.0.1', port=5432),
        pool_size=1, max_overflow=0, connect_args={'connect_timeout': 5,
        'options': '-c default_transaction_read_only=on -c statement_timeout=8000 -c lock_timeout=150'})


def selections(settings):
    legacy = engine(settings, 'DATABASE_URL')
    with legacy.connect() as db:
        row = db.execute(text('''SELECT z.organization_id,z.id AS zone,w.s3_key
            FROM wav_file w JOIN sensor s ON s.id=w.sensor_id JOIN gateway g ON g.id=s.gateway_id
            JOIN zone z ON z.id=g.zone_id WHERE w.id=:id'''), {'id': WAV}).mappings().one()
    legacy.dispose()
    direct = engine(settings, 'DIRECT_DATABASE_URL')
    with direct.connect() as db:
        d = db.execute(text('SELECT organization_id,zone_id FROM direct_device WHERE id=:id'),
                       {'id': DEVICE}).mappings().one()
        manifest = db.execute(text('SELECT manifest FROM direct_revision WHERE segment_id=:id AND revision=601'),
                              {'id': SEGMENT}).scalar_one()
        run = next(i for i, r in enumerate(manifest['runs']) if r['sha256'] == EXPECTED['direct'][0])
    direct.dispose()
    result = {
        'gateway': {'org': row['organization_id'], 'zone': row['zone'],
                    'object': '/greenmind-raw/'+row['s3_key'], 'url': '/api/v1/wav/download/'+WAV},
        'direct': {'org': d['organization_id'], 'zone': d['zone_id'],
                   'object': '/greenmind-direct-production-hotspot/'+manifest['runs'][run]['key'],
                   'url': f'/api/v1/visualization/direct/{DEVICE}/wav/{SEGMENT}/601/{run}'},
    }
    with legacy.connect() as db:
        for item in result.values():
            item['actor'] = str(db.execute(text('''SELECT u.id FROM users u WHERE u.is_active
                AND u.is_verified AND u.organization_id=:org AND (u.role::text IN ('owner','admin')
                OR EXISTS(SELECT 1 FROM zone_access a WHERE a.user_id=u.id AND a.zone_id=:zone)) LIMIT 1'''),
                item).scalar_one())
            item['other'] = str(db.execute(text('''SELECT u.id FROM users u WHERE u.is_active
                AND u.is_verified AND (u.organization_id IS DISTINCT FROM :org OR
                (u.role::text NOT IN ('owner','admin') AND NOT EXISTS
                (SELECT 1 FROM zone_access a WHERE a.user_id=u.id AND a.zone_id=:zone))) LIMIT 1'''),
                item).scalar_one())
    legacy.dispose()
    return result


def main():
    from app.raw_archive.observation import protected

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--restore', type=Path)
    args = parser.parse_args()
    assert os.geteuid() == 0 and os.uname().nodename == 'Biolingo'
    if args.restore:
        assert args.restore.parent == ROOT and args.restore.name.startswith('archive-only-')
        print(json.dumps({'restored': restored(args.restore), 'deleted_files': 0})); return
    wait_reserve(640, 30)
    before = protected()
    settings = environment()
    assert settings['S3_ENDPOINT'] == 'http://minio:9000'
    assert settings['DIRECT_S3_ENDPOINT_URL'] == 'https://green-mind.ch:9444'
    assert settings['RAW_ARCHIVE_READ_BROKER_SOCKET'] == '/broker/broker.sock'
    assert settings['RAW_ARCHIVE_DELETE_ENABLED'] == 'false'
    selected = selections(settings)
    counts = {key: 0 for key in selected}
    folder = ROOT/('archive-only-'+datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f'))
    folder.mkdir(mode=0o700)
    original = (ROOT/'read.env').read_bytes()
    fd = os.open(folder/'before.env', os.O_CREAT|os.O_EXCL|os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'wb') as body: body.write(original)
    lines = original.decode().splitlines()
    changed = '\n'.join(key+'=https://green-mind.ch:9445' if key in
                         {'S3_ENDPOINT', 'DIRECT_S3_ENDPOINT_URL'} else line
                         for line in lines for key in [line.split('=', 1)[0]])+'\n'
    info = {'sha256': hashlib.sha256(original).hexdigest(),
            'fault_sha256': hashlib.sha256(changed.encode()).hexdigest()}
    with (folder/'restore.json').open('x') as body:
        os.fchmod(body.fileno(), 0o600); json.dump(info, body)

    class Fixture(BaseHTTPRequestHandler):
        def log_message(self, *_): pass  # Never log credentials or source keys.
        def do_GET(self):
            path = unquote(urlsplit(self.path).path)
            kind = next((k for k, v in selected.items() if v['object'] == path), None)
            if kind is None:
                self.send_error(403); return
            counts[kind] += 1
            body = b'<Error><Code>NoSuchKey</Code><Message>Private acceptance fixture</Message></Error>'
            self.send_response(404); self.send_header('Content-Type', 'application/xml')
            self.send_header('Content-Length', str(len(body))); self.end_headers()
            if self.command != 'HEAD': self.wfile.write(body)
        do_HEAD = do_GET
        def do_POST(self): self.send_error(405)
        do_PUT = do_POST
        do_DELETE = do_POST

    server = HTTPServer(('172.28.20.1', 9445), Fixture)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain('/etc/letsencrypt/live/greenmind.biolingo.org/fullchain.pem',
                        '/etc/letsencrypt/live/greenmind.biolingo.org/privkey.pem')
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    proof = {'status': 'blocked', 'source_objects_removed': 0, 'public_routes_changed': False,
             'device_bearer_compat_accepted': False, 'deleted_files': 0}
    try:
        command(['systemd-run', '--unit='+TIMER, '--on-active=300', '--property=MemoryMax=128M',
                 '--property=MemorySwapMax=0', '--property=CPUQuota=10%', '--property=RuntimeMaxSec=160',
                 '--property=Environment=PYTHONPATH=/opt/greenmind/archive-preparation/20261010-v23/backend',
                 '/opt/greenmind/raw-copy/20260925/venv/bin/python', str(Path(__file__).resolve()),
                 '--restore', str(folder)])
        command(['docker', 'compose', '-f', str(ROOT/'compose.json'), 'stop', 'api'], 30)
        wait_reserve(896, 60)
        with (ROOT/'read.env').open('w') as body:
            body.write(changed); body.flush(); os.fsync(body.fileno())
        command(['docker', 'compose', '-f', str(ROOT/'compose.json'), 'up', '-d', '--no-build', '--no-deps', 'api'], 35)
        end = time.monotonic()+35
        while True:
            try:
                with urlopen(BASE+'/health', timeout=2) as response:
                    assert response.status == 200; break
            except OSError:
                assert time.monotonic() < end; time.sleep(1)

        def request(item, actor):
            wait_reserve(640, 30)
            headers = {}
            if actor is not None:
                encode = lambda value: base64.urlsafe_b64encode(value).rstrip(b'=')
                payload = encode(b'{"alg":"HS256","typ":"JWT"}')+b'.'+encode(
                    json.dumps({'sub': actor, 'exp': int(time.time())+300}).encode())
                token = (payload+b'.'+encode(hmac.digest(settings['JWT_SECRET_KEY'].encode(), payload, 'sha256'))).decode()
                headers['Cookie'] = 'access_token='+token
            try:
                with urlopen(Request(BASE+item['url'], headers=headers), timeout=25) as response:
                    return response.status, response.read(1048577)
            except HTTPError as error: return error.code, error.read(4096)

        for kind, item in selected.items():
            assert request(item, None)[0] == 401
            assert request(item, item['other'])[0] == 404
            status, payload = request(item, item['actor'])
            assert status == 200 and len(payload) == EXPECTED[kind][1], kind+'_http'
            assert hashlib.sha256(payload).hexdigest() == EXPECTED[kind][0], kind+'_sha'
            with wave.open(io.BytesIO(payload)) as wav:
                assert len(wav.readframes(wav.getnframes())) == wav.getnframes()*wav.getnchannels()*wav.getsampwidth()
            assert counts[kind] > 0, kind+'_fault_not_reached'
            proof[kind+'_dashboard_old_url_archive_only'] = True
        assert protected() == before
        proof['status'] = 'dashboard_archive_only_passed'
    except Exception as error:  # noqa: BLE001 - never expose credential-bearing driver errors
        proof['error_type'] = type(error).__name__
        if isinstance(error, AssertionError) and str(error) in {'gateway_http','gateway_sha','direct_http','direct_sha'}:
            proof['reason'] = str(error)
    finally:
        proof['private_configuration_restored'] = restored(folder)
        proof['receivers_unchanged'] = protected() == before
        proof['fault_requests'] = counts
        server.shutdown(); server.server_close()
        command(['systemctl', 'stop', TIMER+'.timer'])
        with (folder/'proof.json').open('x') as body:
            os.fchmod(body.fileno(), 0o600); json.dump(proof, body)
    print(json.dumps(proof))


if __name__ == '__main__': main()
