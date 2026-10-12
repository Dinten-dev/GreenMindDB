"""One bounded, credential-free offline chunk phase."""
import argparse
import json
import os
import time
from pathlib import Path

from app.raw_archive import catalog_chunks
from app.raw_archive.health import SafetyPause

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=['prepare', 'process', 'assemble'], required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--table')
    parser.add_argument('--chunks', type=Path)
    parser.add_argument('--position', type=int)
    parser.add_argument('--phases', type=Path)
    args = parser.parse_args()
    started = time.monotonic()
    last = float('-inf')
    def checkpoint():
        nonlocal last
        if time.monotonic()-started >= 110:
            raise SafetyPause('offline_phase_deadline')
        if time.monotonic()-last < 0.5:
            return
        available = int(next(x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines()
                             if x.startswith('MemAvailable:')))/1024
        if available < 800 or os.getloadavg()[0] > 2:
            raise SafetyPause('archive_headroom')
        last = time.monotonic()
    if args.phase == 'prepare':
        result = catalog_chunks.prepare(args.source, args.table, args.output, checkpoint)
    elif args.phase == 'process':
        result = catalog_chunks.process(args.source, args.chunks, args.position, args.output, checkpoint)
    else:
        folders = [Path(p) for p in json.loads(args.phases.read_text())]
        result = catalog_chunks.assemble(args.source, args.chunks, folders, args.output, checkpoint)
    print(json.dumps({'phase': args.phase, 'complete': result['complete'],
                      'rows': result['rows'], 'deleted_files': 0}))

if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'status': 'paused' if isinstance(error, SafetyPause) else 'blocked',
            'reason': error.code if isinstance(error, SafetyPause) else type(error).__name__, 'deleted_files': 0}))
        raise SystemExit(75 if isinstance(error, SafetyPause) else 1) from None
