"""Wait without SQL, then one bounded local-source capture.

Only lock contention before output creation is retried. All partial source
snapshots remain private and intact; they are never combined.
"""

import argparse
import json
import time
from pathlib import Path

from app.raw_archive.health import SafetyPause
from app.raw_archive.runner import health_probe
from app.raw_archive.source_capture_once import run


def wait_then_capture(output):
    probe = health_probe()
    end = time.monotonic()+900
    good = 0
    try:
        while time.monotonic() < end:
            good = good+1 if probe() and probe.last.get('load', 99) <= 2.0 else 0
            if good >= 3:
                break
            time.sleep(10)
        else:
            raise SafetyPause('soft_start_headroom')
    finally:
        probe.close()
    for attempt in range(4):
        try:
            return run(output)
        except SafetyPause as error:
            if error.code != 'archive_job_busy' or output.exists() or attempt == 3:
                raise
            time.sleep(20)
    raise AssertionError('unreachable')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(wait_then_capture(args.output)))
    except Exception as error:  # noqa: BLE001 - redact private SQL driver settings
        print(json.dumps({'status': 'paused' if isinstance(error, SafetyPause) else 'blocked',
            'reason': error.code if isinstance(error, SafetyPause) else type(error).__name__,
            'deleted_files': 0}))
        raise SystemExit(75 if isinstance(error, SafetyPause) else 1) from None
