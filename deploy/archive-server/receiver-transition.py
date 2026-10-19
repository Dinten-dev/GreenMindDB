"""Operator-reviewed transition guards. No source data or receiver is stopped."""
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path('/opt/greenmind/peter-receiver-v6-20261010')
SOURCE = Path('/home/traver/greenmind-peter-fixes-v6-20261010')
PROXY = Path('/etc/nginx/sites-available/greenmind-prod')
BEFORE = 'f4e7b516852a52152e03685f59ec1b396c5634af269114e5c81e9c5f28e95436'
AFTER = 'b5dbe4d009cce995c31bb7571413667dfe43130f051ec903d817b5c67d434d6c'
GATE = '11e03dbc34c1ed1b01dff5a151c3d518ef0e2e822e9919184b09e9182210efa5'
FILES = {'switch_receiver.py': 'ff2edb64cf2b24998b0fc980a33b780ddd59c691ae54f14007c438273c15d6ac',
         'switch_receiver_v2.py': '1e03dd40e4c92fe0fc91253b7dc122a922901a860021f9a4579617d0aee2c191',
         'nginx-before.conf': BEFORE, 'nginx-receiver-v6.conf': AFTER,
         'nginx-ws-gate.conf': GATE}
ENV = Path('/etc/greenmind/raw-copy/storagebox.env')
ENV_SHA = '721adf8b2dbd10769886f51ff3718e4a3abc50b7fbab1fbe6ff5f885f078ffa5'
MONITOR = Path('/etc/greenmind/receiver-health/gateway.json')
CONTROL = Path('/opt/greenmind/staging-cutover-20261010/control.py')
CONTROL_SHA = '960a765ce34cd27fe4d0c4a9ea6788742ae6f359bbc654101bfb9043dfddc32c'

def digest(data):
    return hashlib.sha256(data).hexdigest()

def atomic(path, data, mode=0o600):
    tmp = path.with_name(path.name + '.receiver-transition-tmp')
    with tmp.open('xb') as f:
        os.fchmod(f.fileno(), mode)
        f.write(data); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)
    fd = os.open(path.parent, os.O_DIRECTORY)
    os.fsync(fd); os.close(fd)

def retained(name, data):
    path = ROOT / name
    if path.exists():
        assert path.read_bytes() == data
    else:
        atomic(path, data)

def prepare():
    assert digest(PROXY.read_bytes()) == BEFORE
    assert digest(ENV.read_bytes()) == ENV_SHA
    assert digest(CONTROL.read_bytes()) == CONTROL_SHA
    assert json.loads(MONITOR.read_text()) == {'schema': 1, 'ports': [8120]}
    for name, expected in FILES.items():
        data = (SOURCE / name).read_bytes()
        assert digest(data) == expected
        retained(name, data)
    base = (ROOT / 'nginx-before.conf').read_bytes()
    assert base.count(b'127.0.0.1:8120') == 4
    assert base.replace(b'127.0.0.1:8120', b'127.0.0.1:8152') == (ROOT / 'nginx-receiver-v6.conf').read_bytes()
    retained('copier-environment.original', ENV.read_bytes())
    retained('gateway-monitor.original', MONITOR.read_bytes())
    retained('staging-control.original.py', CONTROL.read_bytes())
    original = ENV.read_bytes()
    line = b'RAW_ARCHIVE_HEALTH_URLS=http://127.0.0.1:8000/health,http://127.0.0.1:8003/health'
    assert original.count(line) == 1
    atomic(ENV, original.replace(line, line + b',http://127.0.0.1:8152/health'))
    atomic(MONITOR, b'{"schema":1,"ports":[8120,8152]}\n', 0o644)
    atomic(ROOT / 'copier-cutover-ready.json', json.dumps({'schema': 1,
        'receiver_port': 8152, 'copier_health_updated': True,
        'original_environment_saved': True}).encode())

def recover_gate():
    # Terminal loss can never leave new WebSocket admission gated indefinitely.
    if digest(PROXY.read_bytes()) == GATE:
        atomic(PROXY, (ROOT / 'nginx-before.conf').read_bytes(), 0o644)
        subprocess.run(['nginx', '-t'], check=True, capture_output=True, timeout=10)
        subprocess.run(['systemctl', 'reload', 'nginx'], check=True, timeout=10)
    if digest(PROXY.read_bytes()) == BEFORE:
        for target, name, mode in [(ENV, 'copier-environment.original', 0o600),
                                  (MONITOR, 'gateway-monitor.original', 0o644),
                                  (CONTROL, 'staging-control.original.py', 0o600)]:
            atomic(target, (ROOT / name).read_bytes(), mode)

def activate():
    prepare()
    unit = 'greenmind-receiver-gate-recovery-' + datetime.now(UTC).strftime('%Y%m%dt%H%M%S')
    subprocess.run(['systemd-run', '--unit=' + unit, '--on-active=90',
        '--property=MemoryMax=32M', '--property=MemorySwapMax=0',
        '--property=CPUQuota=5%', '--property=TimeoutStartSec=35',
        '/usr/bin/python3', str(ROOT / 'receiver-transition.py'), 'recover'], check=True)
    try:
        subprocess.run(['/usr/bin/python3', str(ROOT / 'switch_receiver_v2.py'), 'activate'],
                       check=True, timeout=80)
    finally:
        current = digest(PROXY.read_bytes())
        if current == AFTER:
            old = (ROOT / 'staging-control.original.py').read_bytes()
            assert old.count(BEFORE.encode()) == 1
            atomic(CONTROL, old.replace(BEFORE.encode(), AFTER.encode()))
        else:
            recover_gate()
    assert digest(PROXY.read_bytes()) == AFTER
    atomic(ROOT / 'transition-verified.json', json.dumps({'at': datetime.now(UTC).isoformat(),
        'proxy_sha256': AFTER, 'staging_control_sha256': digest(CONTROL.read_bytes()),
        'actual_gateway_ports': [8120, 8152], 'copier_health_port': 8152,
        'deleted_files': 0, 'old_receiver_stopped': False}).encode())
    print('Accepted gateway transition; all original receivers retained.')

if __name__ == '__main__':
    import sys
    assert os.geteuid() == 0 and os.uname().nodename == 'Biolingo'
    assert ROOT.stat().st_uid == 0 and not ROOT.stat().st_mode & 0o077
    if sys.argv[1:] == ['recover']:
        recover_gate()
    elif sys.argv[1:] == ['activate']:
        activate()
    else:
        raise SystemExit('Exact activate/recover action required')
