"""Host-side telemetry bridge. No delete API, arbitrary commands or public listener.

Socket is mounted ONLY into the allowlisted administration container. The bridge
can start one existing copy-only service, never any receiver or retention unit.
"""
import datetime
import http.server
import json
import os
import socketserver
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

UNIT = 'greenmind-raw-copy.service'
ROOT = Path('/var/lib/greenmind-raw-copy')
SOCKET = Path('/run/greenmind-archive-monitor/bridge.sock')
FLAGS = ('RAW_ARCHIVE_DELETE_ENABLED', 'RAW_ARCHIVE_READS_ACCEPTED',
         'RAW_ARCHIVE_READS_ENABLED', 'RETENTION_ENABLED', 'DIRECT_RETENTION_ENABLED')
state = {}
lock = threading.Lock()
last_start = 0.0


def run(args, timeout=3, data=None):
    return subprocess.check_output(args, input=data, text=True, timeout=timeout,
                                   stderr=subprocess.DEVNULL)


def config():
    return dict(line.split('=', 1) for line in
                Path('/etc/greenmind/raw-copy/storagebox.env').read_text().splitlines()
                if '=' in line and not line.startswith('#'))


def copy_safe():
    values = config()
    if any(values.get(key) != 'false' for key in FLAGS):
        return False
    launcher = Path('/opt/greenmind/raw-copy/20260925/adaptive-copy.py')
    info = launcher.stat()
    # Root-owned launcher must force every destructive flag off.
    source = launcher.read_text()
    return (info.st_uid == 0 and not info.st_mode & 0o022
            and all(repr(key) in source for key in FLAGS)
            and "os.environ[name] = 'false'" in source
            and str(launcher) in run(['systemctl', 'show', UNIT, '-p', 'ExecStart', '--value']))


def collect():
    props = dict(line.split('=', 1) for line in run([
        'systemctl', 'show', UNIT, '-p', 'ActiveState', '-p', 'MemoryCurrent',
        '-p', 'MemoryMax']).splitlines() if '=' in line)
    with sqlite3.connect(f'file:{ROOT}/archive.sqlite3?mode=ro', uri=True, timeout=1) as db:
        db.execute('PRAGMA query_only=ON')
        count, size = db.execute("""SELECT count(*),coalesce(sum(
            json_extract(receipt,'$.recording.size')),0) FROM archive
            WHERE state='verified'""").fetchone()
        pending = db.execute('SELECT count(*) FROM backup_work WHERE active=1').fetchone()[0]
    active = props['ActiveState'] in ('active', 'activating')
    latest = run(['journalctl', '-u', UNIT, '-n', '15', '-o', 'cat', '--no-pager'])
    paused = not active and 'preserve receiver headroom' in latest
    available = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines()
                         if line.startswith('MemAvailable:'))) * 1024
    return dict(environment='production', configured=True, files_copied=count,
                copied_bytes=size, pending_bytes=None, pending_references=pending,
                transfer_bytes_per_second=None,
                worker_memory_bytes=int(props['MemoryCurrent']) if props['MemoryCurrent'].isdigit() else 0,
                worker_memory_limit_bytes=int(props['MemoryMax']),
                worker_memory_reserve_bytes=max(128*1024**2, int(available*.2)),
                state='running' if active else 'paused' if paused else
                'failed' if props['ActiveState']=='failed' else 'idle',
                manual_copy_available=copy_safe(),
                sampled_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                message=f'{pending:,} Katalogeinträge ausstehend; Gesamtgrösse noch nicht bestimmt. '
                        'Lokale WAV-Dateien bleiben erhalten.')


def quota():
    c = config()
    output = run(['runuser', '-u', 'greenmind-raw-copy', '--', '/usr/bin/sftp',
                  '-F', '/dev/null', '-q', '-b', '-', '-P', c.get('RAW_ARCHIVE_SFTP_PORT','23'),
                  '-i', c['RAW_ARCHIVE_SFTP_KEY'], '-oBatchMode=yes', '-oIdentitiesOnly=yes',
                  '-oStrictHostKeyChecking=yes', '-oConnectTimeout=5',
                  '-oUserKnownHostsFile='+c['RAW_ARCHIVE_SFTP_KNOWN_HOSTS'],
                  c['RAW_ARCHIVE_SFTP_USER']+'@'+c['RAW_ARCHIVE_SFTP_HOST']], timeout=10, data='df .\n')
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 5 and all(v.isdigit() for v in parts[:4]):
            return int(parts[0])*1024, int(parts[1])*1024
    return None, None


def sampler():
    global state
    previous = None
    box = (None, None)
    box_at = 0.0
    while True:
        started = time.monotonic()
        try:
            fresh = collect()
            if started-box_at >= 60:
                try:
                    box = quota()
                except Exception:
                    box = (None, None)
                box_at = started
            fresh.update(storage_box_total_bytes=box[0], storage_box_used_bytes=box[1])
            if previous:
                fresh['transfer_bytes_per_second'] = max(0, fresh['copied_bytes']-previous[1]) / (started-previous[0])
            previous = started, fresh['copied_bytes']
            with lock:
                state = fresh
        except Exception:
            with lock:
                state = {}
        time.sleep(max(1, 5-(time.monotonic()-started)))


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, code, value):
        data=json.dumps(value).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        with lock:
            value=dict(state)
        self.reply(200 if value and self.path=='/status' else 503, value)

    def do_POST(self):
        global last_start
        if self.path != '/copy' or self.headers.get('Content-Length','0') != '0':
            self.reply(400, {})
            return
        try:
            with lock:
                if not state or not copy_safe():
                    self.reply(503, {})
                    return
                active = run(['systemctl','show',UNIT,'-p','ActiveState','--value']).strip()
                if active in ('active','activating') or time.monotonic()-last_start < 60:
                    self.reply(200, {'accepted':True,'message':'Kopierlauf bereits angefordert.'})
                    return
                run(['systemctl','start','--no-block',UNIT])
                last_start=time.monotonic()
            self.reply(200, {'accepted':True,'message':'Kopierlauf angefordert. Keine Löschung.'})
        except Exception:
            self.reply(503, {})


class Server(socketserver.UnixStreamServer):
    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(3)
        return connection, address


if __name__ == '__main__':
    SOCKET.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    SOCKET.unlink(missing_ok=True)
    with Server(str(SOCKET), Handler) as server:
        os.chown(SOCKET, 0, 10001)
        os.chmod(SOCKET, 0o660)
        threading.Thread(target=sampler, daemon=True).start()
        server.serve_forever()
