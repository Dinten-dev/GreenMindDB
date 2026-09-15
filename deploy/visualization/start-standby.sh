#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
python3 - <<'PY'
from pathlib import Path
values={line.split(':')[0]:int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith(('MemAvailable:','SwapFree:','SwapTotal:'))}
assert values['SwapTotal'] >= 2*1024**2, 'Speicherreserve fehlt: zuerst prepare-memory.sh ausführen'
assert values['MemAvailable']+values['SwapFree'] >= 2*1024**2, 'Zu wenig freie Speicherreserve'
assert not Path('state/enable-pruning').exists(), 'Pruning-Marker existiert bereits: bestehenden Rollout prüfen'
PY
docker compose -f compose.yml config -q
# No migration of the existing app and no restart of its containers.
docker compose -f compose.yml run --rm --no-deps --entrypoint python visual-worker -m app.visualization.worker init
docker compose -f compose.yml up -d --no-build
python3 - <<'PY'
import time,urllib.request
from pathlib import Path
values=dict(line.split('=',1) for line in Path('.env').read_text().splitlines())
for port,path in ((values['VISUAL_API_PORT'],'/health'),(values['VISUAL_FRONTEND_PORT'],'/de/app/sensors')):
    for attempt in range(12):
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{port}{path}',timeout=5) as response:
                assert response.status==200
            break
        except Exception:
            if attempt==11: raise
            time.sleep(2)
print('Standby bereit. Öffentlicher Proxy unverändert, historische Entfernung deaktiviert.')
PY
