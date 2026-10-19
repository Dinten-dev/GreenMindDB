"""Prepare fixed transition health checks, without changing active routes.

Only telemetry restarts. The current gateway configuration remains [8120].
The existing forced-command identity retains its key, IP restriction and rights.
"""

import hashlib
import json
import os
import pwd
import shutil
import subprocess
import tarfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request, urlopen

OLD = Path('/opt/greenmind/archive-preparation/20261008-v8')
NEW = Path('/opt/greenmind/archive-preparation/20261010-health-v9')
PROBE = Path('/opt/greenmind/source-probe/source_probe.py')
PROXY = Path('/etc/nginx/sites-available/greenmind-prod')
PROXY_SHA = 'f4e7b516852a52152e03685f59ec1b396c5634af269114e5c81e9c5f28e95436'
PAYLOAD_SHA = '1d02c9079d9ab92af8b2d24c4b86bf3ff72bdd1782307aad1cf8d83244a1f679'
OLD_PROBE_SHA = '8286eab9a679deb5e5bc1c399aafb2da6f9b34115a7053e0fa890de3dcf021db'
OLD_LEASE_SHA = '761b9ed82e2f0ac3a63f9d6c99776e5737309c6e2ade65264633458aac27832f'
RECEIVERS = ('gm-zones-production-2e687bec010a-application-1',
             'gm-direct-production-direct-api-1', 'greenminddb-backend-1')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def state():
    return subprocess.check_output(['docker', 'inspect', '--format',
        '{{.Name}} {{.State.Running}} {{.State.StartedAt}} {{.RestartCount}}',
        *RECEIVERS], timeout=10)


def atomic(path, data, *, mode=0o644, uid=0, gid=0):
    temporary = path.with_name(path.name + '.health-v9-new')
    with temporary.open('xb') as body:
        os.fchmod(body.fileno(), mode)
        os.fchown(body.fileno(), uid, gid)
        body.write(data)
        body.flush()
        os.fsync(body.fileno())
    os.replace(temporary, path)


def main():
    assert os.geteuid() == 0 and os.uname().nodename == 'Biolingo'
    assert sha(PROXY.read_bytes()) == PROXY_SHA
    before = state()
    assert b' false ' not in before
    account = pwd.getpwnam('greenmind-archive-lease')
    busy = subprocess.run(['pgrep', '-u', str(account.pw_uid)],
                          stdout=subprocess.DEVNULL, timeout=5)
    assert busy.returncode == 1, 'Retain an active source lease; retry later'
    keys = Path(account.pw_dir) / '.ssh/authorized_keys'
    raw = keys.read_bytes()
    pointer = ('PYTHONPATH=' + str(OLD) + '/backend').encode()
    assert raw.count(pointer) == 1 and raw.count(b'command=') == 1
    assert b'from="2.31.22.33",restrict,' in raw
    old_probe = PROBE.read_bytes()
    old_lease = OLD / 'backend/app/raw_archive/remote_lease.py'
    assert sha(old_probe) == OLD_PROBE_SHA and sha(old_lease.read_bytes()) == OLD_LEASE_SHA
    payload = Path(__file__).parent / 'receiver-health-v9.tar.gz'
    assert sha(payload.read_bytes()) == PAYLOAD_SHA
    with tarfile.open(payload) as tar:
        members = tar.getmembers()
        assert {m.name for m in members} == {'files.json', 'source_probe.py', 'remote_lease.py'}
        assert all(m.isfile() and m.size <= 20000 for m in members)
        parts = {m.name: tar.extractfile(m).read() for m in members}
    hashes = json.loads(parts['files.json'])
    assert set(hashes) == {'source_probe.py', 'remote_lease.py'}
    assert all(sha(parts[name]) == digest for name, digest in hashes.items())
    assert all(not p.is_symlink() for p in OLD.rglob('*'))
    if not NEW.exists():
        shutil.copytree(OLD, NEW)
        for name in hashes:
            atomic(NEW / 'backend/app/raw_archive' / name, parts[name])
        manifest = json.loads((NEW / 'files.json').read_text())
        for name in hashes:
            manifest['backend/app/raw_archive/' + name] = hashes[name]
        atomic(NEW / 'files.json', json.dumps(manifest, sort_keys=True).encode())
    manifest = json.loads((NEW / 'files.json').read_text())
    assert all(manifest['backend/app/raw_archive/' + name] == hashes[name] for name in hashes)
    assert all(sha((NEW / name).read_bytes()) == digest for name, digest in manifest.items())
    backup = NEW / 'operator-evidence'
    backup.mkdir(mode=0o700, exist_ok=True)
    for name, data in (('source_probe.original.py', old_probe), ('authorized_keys.original', raw)):
        retained = backup / name
        if retained.exists():
            assert retained.read_bytes() == data
        else:
            atomic(retained, data, mode=0o600)
    assert not (backup / 'verified.json').exists(), 'Inspect an already accepted upgrade'
    config_dir = Path('/etc/greenmind/receiver-health')
    config_dir.mkdir(mode=0o755, exist_ok=True)
    assert not config_dir.is_symlink() and config_dir.stat().st_uid == 0
    config = config_dir / 'gateway.json'
    if config.exists():
        assert not config.is_symlink() and config.stat().st_uid == 0
        assert config.read_bytes() == b'{"schema":1,"ports":[8120]}\n'
    else:
        atomic(config, b'{"schema":1,"ports":[8120]}\n')
    try:
        assert keys.read_bytes() == raw and PROBE.read_bytes() == old_probe
        atomic(PROBE, parts['source_probe.py'])
        atomic(keys, raw.replace(pointer, ('PYTHONPATH=' + str(NEW) + '/backend').encode()),
               mode=0o600, uid=account.pw_uid, gid=account.pw_gid)
        subprocess.run(['systemctl', 'restart', 'greenmind-source-probe.service'],
                       check=True, timeout=15)
        token = Path('/etc/greenmind/source-probe/token').read_text().strip()
        request = Request('http://127.0.0.1:8150/source-health',
                          headers={'Authorization': 'Bearer ' + token})
        import time
        for attempt in range(30):
            try:
                with urlopen(request, timeout=3) as response:
                    proof = json.loads(response.read(8193))
                break
            except OSError:
                if attempt == 29:
                    raise
                time.sleep(1)
        assert proof['routes'] == {'8120': 200, '8003': 200, '8000': 200}
        assert proof['gateway_receivers'] == {'8120': 200}
        assert state() == before and sha(PROXY.read_bytes()) == PROXY_SHA
    except BaseException:
        atomic(PROBE, old_probe)
        atomic(keys, raw, mode=0o600, uid=account.pw_uid, gid=account.pw_gid)
        subprocess.run(['systemctl', 'restart', 'greenmind-source-probe.service'],
                       check=False, timeout=15)
        raise
    report = {'at': datetime.now(UTC).isoformat(), 'prepared_gateway_ports': [8120, 8152],
        'active_gateway_ports': [8120], 'new_admins': 0, 'key_permissions_changed': False,
        'source_minimum_mib': 640, 'source_maximum_load': 2.4,
        'receivers_unchanged': True, 'proxy_unchanged': True, 'deleted_files': 0,
        'runtime': str(NEW), 'previous_authorization_sha256': sha(raw),
        'current_authorization_sha256': sha(keys.read_bytes())}
    atomic(backup / 'verified.json', json.dumps(report, indent=2).encode(), mode=0o600)
    print(json.dumps(report))


if __name__ == '__main__':
    main()
