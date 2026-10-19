"""Wait without SQL, then one guarded capture; bounded retries for lock contention.

The original source lease/statement/active-phase limits stay unchanged. Partial
outputs are retained and never combined into a completed source snapshot.
"""

import argparse
import json
import sys
import time
from pathlib import Path

from app.raw_archive.archive_host_catalog import export_source
from app.raw_archive.health import SafetyPause
from app.raw_archive.runner import health_probe


def run(output):
    probe = health_probe()
    end = time.monotonic()+900
    consecutive = 0
    try:
        while time.monotonic() < end:
            if probe():
                with probe.local.client.stream('GET', probe.url,
                        headers={'Authorization': 'Bearer '+probe.token}) as response:
                    data = response.read()
                    assert response.status_code == 200 and len(data) <= 8192
                    value = json.loads(data)
                consecutive = consecutive+1 if float(value['load']) <= 2.0 else 0
                if consecutive >= 3:
                    break
            else:
                consecutive = 0
            time.sleep(10)
        else:
            raise SafetyPause('soft_start_headroom')
    finally:
        probe.close()
    for attempt in range(4):
        try:
            return export_source('gateway', output, capture_only=True)
        except SafetyPause as error:
            if error.code != 'archive_job_busy' or output.exists() or attempt == 3:
                raise
            time.sleep(20)  # Busy attempts opened neither output nor SQL.
    raise AssertionError('unreachable')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.output)))
    except Exception as error:  # noqa: BLE001 - redact credentials in driver errors
        print(json.dumps({'status': 'paused' if isinstance(error, SafetyPause) else 'blocked',
            'reason': error.code if isinstance(error, SafetyPause) else type(error).__name__,
            'deleted_files': 0}))
        sys.exit(75 if isinstance(error, SafetyPause) else 1)
