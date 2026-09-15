#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
# A failed check never creates an activation marker.
docker compose -f compose.yml exec -T visual-api python - < validate-standby.py > validation-passed.json.pending
python3 - <<'PY'
import json,os,subprocess
from pathlib import Path
report=json.loads(Path('validation-passed.json.pending').read_text())
assert report['passed'] and len(report['checks'])==5
for name, started in json.loads(Path('before-containers.json').read_text()).items():
    current=json.loads(subprocess.check_output(['docker','inspect',name]))[0]
    assert current['State']['Running'] and current['State']['StartedAt']==started, f'{name} changed or stopped'
report['existing_services_unchanged']=True
Path('validation-passed.json.pending').write_text(json.dumps(report,indent=2)+'\n')
os.replace('validation-passed.json.pending','validation-passed.json')
print(json.dumps(report,indent=2))
PY
