"""Explicit inventory, archive, proof, restore and activation-controlled eviction."""

import argparse
import json
from datetime import datetime
from pathlib import Path

from app.raw_archive.policy import ArchiveBlocked

from . import runtime
from .policy import POLICY_SHA256, utc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    sub.add_parser("status")
    sub.add_parser("bindings")
    sub.add_parser("catalog-backup")
    restore_db = sub.add_parser("restore-database")
    restore_db.add_argument("--folder", type=Path, required=True)
    restore_db.add_argument("--profile-file", type=Path, required=True)
    restore_db.add_argument("--manifest-sha256", required=True)
    restore_db.add_argument("--execute", action="store_true")
    catalogue = sub.add_parser("catalog-restore")
    catalogue.add_argument("--reference", type=Path, required=True)
    catalogue.add_argument("--reference-sha256", required=True)
    inventory = sub.add_parser("inventory")
    inventory.add_argument("--source", choices=("gateway", "direct"), required=True)
    backup = sub.add_parser("backup")
    backup.add_argument("--source", choices=("gateway", "direct"), required=True)
    sql = sub.add_parser("archive-sql")
    sql.add_argument("--dataset", required=True)
    sql.add_argument("--organization", required=True)
    sql.add_argument("--start", type=utc, required=True)
    sql.add_argument("--end", type=utc, required=True)
    wav = sub.add_parser("archive-wav")
    wav.add_argument("--source", choices=("gateway", "direct"), required=True)
    wav.add_argument("--identity", required=True)
    wav.add_argument("--revision", type=int)
    for name in ("verify", "evict", "restore"):
        command = sub.add_parser(name)
        command.add_argument("--identity", required=True)
        if name == "verify":
            command.add_argument("--snapshot-id", type=int, required=True)
        if name == "restore":
            command.add_argument("--output", type=Path, required=True)
    tick = sub.add_parser("tick")
    tick.add_argument("--schedule", type=Path, required=True)
    args = parser.parse_args()
    if args.operation == "status":
        from .ledger import Ledger

        ledger = Ledger(runtime.state_root())
        try:
            result = {
                "policy": POLICY_SHA256,
                "runtime": runtime.runtime_sha256(),
                "states": ledger.status(),
            }
        finally:
            ledger.close()
    elif args.operation == "bindings":
        from app.raw_archive.runner import destination_from_environment

        from .storage import Store

        result = {"bindings_sha256": runtime.bindings_sha256(Store(destination_from_environment()))}
    elif args.operation == "restore-database":
        if not args.execute:
            result = {"status": "REVIEW_ONLY", "source_accesses": 0}
        else:
            import os
            import stat

            from .recovery import restore_database

            descriptor = os.open(args.profile_file, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "r") as body:
                info = os.fstat(body.fileno())
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_mode & 0o077
                    or info.st_uid not in {0, os.getuid()}
                ):
                    raise ArchiveBlocked("Private restore database profile required")
                url = body.read(8193).strip()
                if len(url) > 8192:
                    raise ArchiveBlocked("Restore profile exceeds budget")
            result = restore_database(
                args.folder, url, args.manifest_sha256, checkpoint=lambda: None
            )
    elif args.operation in {"catalog-backup", "catalog-restore"}:
        from app.raw_archive.policy import checksum

        from .registry_backup import backup, restore

        with runtime.worker() as (ledger, store, checkpoint):
            if args.operation == "catalog-backup":
                result = backup(ledger, store, checkpoint=checkpoint)
            else:
                if checksum(args.reference) != args.reference_sha256:
                    raise ArchiveBlocked("Recovery reference changed")
                result = restore(
                    json.loads(args.reference.read_text()),
                    ledger,
                    store,
                    checkpoint=checkpoint,
                    expected_environment=runtime.environment(),
                )
    elif args.operation == "backup":
        from .execution import bounded_job

        with bounded_job(runtime.state_root(), kind="backup-" + args.source):
            result = runtime.backup(args.source)
    elif args.operation == "inventory":
        from .source import Source

        with runtime.worker() as (_, _, checkpoint):
            result = Source(
                runtime.engine(args.source),
                args.source,
                runtime.environment(),
                checkpoint=checkpoint,
            ).inventory()
    elif args.operation == "archive-sql":
        from .source import DATASETS

        if args.dataset not in DATASETS:
            raise ArchiveBlocked("Unclassified dataset")
        result = runtime.publish_sql(
            DATASETS[args.dataset].source, args.dataset, args.organization, args.start, args.end
        )
    elif args.operation == "archive-wav":
        if args.source == "direct" and args.revision is None:
            parser.error("Direct requires an exact revision")
        reference = [args.identity] if args.source == "gateway" else [args.identity, args.revision]
        result = runtime.publish_wav(args.source, reference)
    elif args.operation == "verify":
        result = runtime.verify(args.identity, args.snapshot_id)
    elif args.operation == "evict":
        result = runtime.evict(args.identity)
    elif args.operation == "restore":
        from .recovery import restore

        with runtime.worker() as (ledger, store, checkpoint):
            result = restore(args.identity, args.output, ledger, store, checkpoint=checkpoint)
    else:
        from .execution import bounded_job
        from .schedule import tick

        with bounded_job(runtime.state_root()):
            result = tick(args.schedule)
    # Routine output contains aggregate counts/identities, never raw row bodies,
    # credentials, object keys, personal data or activation-file contents.
    if isinstance(result, dict) and "files" in result:
        result = {"status": result["state"], "identity": result["candidate"]}
    elif isinstance(result, dict) and "receipt" in result:
        result = {"status": result["receipt"]["state"], "identity": result["receipt"]["candidate"]}
    print(
        json.dumps(
            result,
            sort_keys=True,
            default=lambda value: value.isoformat() if isinstance(value, datetime) else str(value),
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        import sys

        result = {"status": "BLOCKED", "error": type(error).__name__}
        if isinstance(error, ArchiveBlocked):
            result["reason"] = str(error)
        print(json.dumps(result, sort_keys=True), file=sys.stderr)
        raise SystemExit(1) from None
