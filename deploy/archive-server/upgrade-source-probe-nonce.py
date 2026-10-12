"""Hash-guarded private telemetry correction; preserve all receivers and routes."""
import hashlib
import json
import os
import subprocess
from pathlib import Path
from urllib.request import urlopen

OLD = '6caf643d0ac699b35cc7eb15070e400e495c15d02e7d281a0f70fab76d787cfb'
NEW = 'd7476ba2285fd0771c29cdc10770a641e1096dfda90aef3d827e2a73ddd812dd'
PROXY = Path('/etc/nginx/sites-available/greenmind-prod')
BASE = Path('/opt/greenmind/source-probe-nonce-20261010-v46')
NAMES = ['gm-zones-production-2e687bec010a-application-1',
         'gm-peter-receiver-20261010', 'gm-direct-production-direct-api-1',
         'greenminddb-backend-1']

def receivers():
    rows = json.loads(subprocess.check_output(['docker','inspect',*NAMES], timeout=10))
    return {r['Name']: {'image':r['Image'], 'state':{k:r['State'][k] for k in ('Running','StartedAt','Pid')},
                       'restarts':r['RestartCount']} for r in rows}

def main():
    assert os.geteuid() == 0 and os.uname().nodename == 'Biolingo'
    assert hashlib.sha256(Path('/opt/greenmind/source-probe/source_probe.py').read_bytes()).hexdigest() == OLD
    code = Path('/home/traver/source-probe-v46.py').read_bytes()
    assert hashlib.sha256(code).hexdigest() == NEW
    available = int(next(l.split()[1] for l in Path('/proc/meminfo').read_text().splitlines()
                         if l.startswith('MemAvailable:'))) / 1024
    assert available >= 688 and os.getloadavg()[0] <= 2.4
    baseline, proxy = receivers(), PROXY.read_bytes()
    assert all(r['state']['Running'] for r in baseline.values())
    for port in (8120,8152,8003,8000):
        with urlopen(f'http://127.0.0.1:{port}/health', timeout=2) as r:
            assert r.status == 200
    BASE.mkdir(mode=0o755)
    (BASE/'source_probe.py').write_bytes(code)
    (BASE/'source_probe.py').chmod(0o644)
    drop = Path('/etc/systemd/system/greenmind-source-probe.service.d')
    drop.mkdir(exist_ok=True)
    target = drop/'nonce-v46.conf'
    assert not target.exists()
    target.write_text('[Service]\nExecStart=\nExecStart=/usr/bin/python3 '+str(BASE/'source_probe.py')+'\n')
    target.chmod(0o644)
    try:
        subprocess.run(['systemctl','daemon-reload'],check=True,timeout=15)
        subprocess.run(['systemctl','restart','greenmind-source-probe.service'],check=True,timeout=20)
        assert subprocess.check_output(['systemctl','is-active','greenmind-source-probe.service'],timeout=5).strip()==b'active'
        assert receivers() == baseline and PROXY.read_bytes() == proxy
    except Exception:
        target.rename(BASE/'failed-dropin.conf')
        subprocess.run(['systemctl','daemon-reload'],check=True,timeout=15)
        subprocess.run(['systemctl','restart','greenmind-source-probe.service'],check=True,timeout=20)
        raise
    proof = {'complete':True,'old_sha256':OLD,'new_sha256':NEW,
             'receiver_states_unchanged':True,'proxy_unchanged':True,'deleted_files':0}
    (BASE/'verified.json').write_text(json.dumps(proof))
    (BASE/'verified.json').chmod(0o600)
    print(json.dumps(proof))

if __name__ == '__main__':
    main()
