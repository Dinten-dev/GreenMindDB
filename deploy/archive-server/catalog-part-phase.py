"""One bounded private catalog phase; never schedules, routes or deletion."""

import argparse
import json
import os
import pwd
import time
import tempfile
from contextlib import nullcontext
from pathlib import Path

from app.raw_archive import archive_host_catalog, catalog_transfer
from app.raw_archive.health import SafetyPause
from app.raw_archive.policy import ArchiveBlocked
from app.raw_archive.read_only_box import configured_catalog_read_only_box
from app.raw_archive.remote_lease import catalog_lease
from app.raw_archive.runner import destination_from_environment, health_probe


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', required=True, choices=('prepare', 'publish-part',
        'publish-manifest', 'read-manifest', 'restore-part', 'finish'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--reference', type=Path)
    parser.add_argument('--position', type=int)
    for kind in ('ledger', 'gateway', 'direct'):
        parser.add_argument('--'+kind+'-stage', type=Path)
    args = parser.parse_args()
    publishing = args.phase.startswith('publish')
    expected = 'greenmind-archive-copy' if publishing or args.phase == 'prepare' else 'greenmind-archive-read'
    if (os.uname().nodename != 'ubuntu-4gb-fsn1-1'
            or os.getuid() != pwd.getpwnam(expected).pw_uid):
        raise ArchiveBlocked('Exact private archive account required')
    archive_host_catalog.configured()
    if any(os.getenv(k) != 'false' for k in ('RETENTION_ENABLED',
            'DIRECT_RETENTION_ENABLED', 'VISUAL_PRUNE_ENABLED')):
        raise ArchiveBlocked('Destructive policies must remain disabled')
    if not publishing and args.phase != 'prepare' and any(
            k.startswith('RAW_ARCHIVE_SFTP_') for k in os.environ):
        raise ArchiveBlocked('Independent restoration must not receive upload access')
    probe, box, reader = health_probe(), None, None
    finish_guard = None
    started = time.monotonic()
    def checkpoint():
        if time.monotonic()-started >= 110:
            raise SafetyPause('catalog_part_deadline')
        if not probe():
            raise SafetyPause(probe.last.get('reason', 'headroom'), probe.last)
    try:
        checkpoint()
        if args.phase == 'finish':
            from app.raw_archive.catalog_finish_guard import CatalogFinishGuard
            finish_guard = CatalogFinishGuard(probe, deadline=started+110).start()
            checkpoint = finish_guard
        with catalog_lease() if publishing else nullcontext(lambda: None) as check_lease:
            def guarded():
                check_lease()
                checkpoint()
            guarded()
            if publishing:
                if args.manifest is None:
                    raise ArchiveBlocked('Complete manifest required')
                box = destination_from_environment()
                # UNIX sockets have a small pathname limit. Keep only the
                # ephemeral transport in PrivateTmp; catalog files stay put.
                with tempfile.TemporaryDirectory(prefix='gm-catalog-') as transport:
                    with box.session(Path(transport)):
                        result = (catalog_transfer.publish_part(args.manifest, args.position, box, guarded)
                            if args.phase == 'publish-part' else
                            catalog_transfer.publish_manifest(args.manifest, box, guarded))
            elif args.phase == 'prepare':
                stages = {k: getattr(args, k+'_stage') for k in ('ledger', 'gateway', 'direct')}
                if any(p is None for p in stages.values()):
                    raise ArchiveBlocked('Every completed source stage required')
                result = catalog_transfer.prepare(stages, args.output, guarded)
            else:
                reader = configured_catalog_read_only_box()
                if reader is None:
                    raise ArchiveBlocked('Dedicated read-only catalog access required')
                if args.phase == 'restore-part':
                    result = catalog_transfer.restore_part(args.output, args.position, reader, guarded)
                else:
                    if args.reference is None:
                        raise ArchiveBlocked('Exact private publication reference required')
                    reference = catalog_transfer.private_json(args.reference)
                    result = (catalog_transfer.read_manifest(reader, reference, args.output, guarded)
                        if args.phase == 'read-manifest' else
                        catalog_transfer.finish(args.output, reference, reader, guarded))
            print(json.dumps(result))
    finally:
        if finish_guard is not None:
            finish_guard.close()
        else:
            probe.close()
        if box is not None:
            box.close()
        if reader is not None:
            reader.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as error:  # noqa: BLE001 - never expose private driver settings
        print(json.dumps({'status': 'paused' if isinstance(error, SafetyPause) else 'blocked',
            'reason': error.code if isinstance(error, SafetyPause) else type(error).__name__,
            'deleted_files': 0}))
        raise SystemExit(75 if isinstance(error, SafetyPause) else 1) from None
