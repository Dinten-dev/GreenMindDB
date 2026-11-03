"""Explicit inventory, archive, proof, restore and activation-controlled eviction."""

import argparse
import json
from datetime import datetime
from pathlib import Path

from app.raw_archive.policy import ArchiveBlocked

from . import runtime
from .policy import POLICY_SHA256, utc


def with_source_catalogue_lease(callback, *, job_kind="copy"):
    import os

    from app.raw_archive.coordination import bound_job_lock, reuse_or_lease
    from app.raw_archive.remote_lease import bound_source_lease, catalog_lease

    if job_kind not in {"copy", "catalog"}:
        raise ArchiveBlocked("Unsupported source catalogue job kind")
    if runtime.environment() != "production":
        return callback()
    if os.getenv("RAW_ARCHIVE_WORKER_ROLE") != "archive":
        raise ArchiveBlocked("Production snapshot lifecycle requires the archive worker role")
    with reuse_or_lease(job_kind):
        bound_job_lock()  # A missing coordination directory must never open the source lease.
        with catalog_lease() as check, bound_source_lease(check):
            return callback()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    sub.add_parser("status")
    sub.add_parser("bindings")
    sub.add_parser("catalog-backup")
    sub.add_parser("snapshot-scope-activate")
    sub.add_parser("snapshot-scope-reconcile")
    sub.add_parser("snapshot-cycle")
    for operation in (
        "snapshot-pin-acquire",
        "snapshot-pin-release-frozen",
        "snapshot-pin-release-completed",
    ):
        pin = sub.add_parser(operation)
        pin.add_argument("--operation-file", type=Path, required=True)
        pin.add_argument("--operation-sha256", required=True)
        if operation == "snapshot-pin-release-frozen":
            pin.add_argument("--frozen-file", type=Path, required=True)
            pin.add_argument("--frozen-sha256", required=True)
        if operation in {"snapshot-pin-release-frozen", "snapshot-pin-release-completed"}:
            pin.add_argument("--termination-bundle-file", type=Path, required=True)
            pin.add_argument("--termination-bundle-sha256", required=True)
    for operation in ("restore-database", "restore-backup"):
        restore_db = sub.add_parser(operation)
        restore_db.add_argument("--folder", type=Path, required=True)
        restore_db.add_argument("--profile-file", type=Path, required=True)
        restore_db.add_argument("--manifest-sha256", required=True)
        restore_db.add_argument("--execute", action="store_true")
        if operation == "restore-backup":
            restore_db.add_argument("--key-file", type=Path, required=True)
    catalogue = sub.add_parser("catalog-restore")
    catalogue.add_argument("--reference", type=Path, required=True)
    catalogue.add_argument("--reference-sha256", required=True)
    inventory = sub.add_parser("inventory")
    inventory.add_argument("--source", choices=("gateway", "direct"), required=True)
    backup = sub.add_parser("backup")
    backup.add_argument("--source", choices=("gateway", "direct"), required=True)
    backup.add_argument(
        "--format",
        choices=(
            "schema-master-v2",
            "postgres-full-v1",
            "schema-master-provider-v1",
            "schema-master-provider-sql-audit-v3",
        ),
        default="schema-master-v2",
    )
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
    sql_fence = sub.add_parser("sql-fence-plan")
    sql_fence.add_argument("--identity", required=True)
    audit_plan = sub.add_parser("sql-audit-plan")
    audit_plan.add_argument("--writer-role", required=True)
    audit_v3_plan = sub.add_parser("sql-audit-v3-plan")
    audit_v3_plan.add_argument("--writer-role", required=True)
    audit_v3_plan.add_argument("--epoch", required=True)
    audit_v3_plan.add_argument("--genesis-sha256", required=True)
    sub.add_parser("sql-scope-prepare-registry")
    for operation in (
        "sql-evict",
        "sql-evict-v3",
        "sql-source-approval-v3",
        "sql-scope-bootstrap",
        "sql-scope-reconcile",
        "sql-scope-bootstrap-v3",
        "sql-scope-reconcile-v3",
    ):
        sql_evict = sub.add_parser(operation)
        sql_evict.add_argument("--identity", required=True)
        for field in ("restore-profile", "proof", "authorization", "snapshot-token"):
            sql_evict.add_argument("--" + field + "-file", type=Path, required=True)
            sql_evict.add_argument("--" + field + "-sha256", required=True)
        if operation == "sql-source-approval-v3":
            for field in ("binding", "dba-identity"):
                sql_evict.add_argument("--" + field + "-file", type=Path, required=True)
                sql_evict.add_argument("--" + field + "-sha256", required=True)
            sql_evict.add_argument("--producer-sha256", required=True)
            sql_evict.add_argument("--challenge-file", type=Path, required=True)
            sql_evict.add_argument("--frame-file", type=Path, required=True)
        elif operation not in {"sql-evict", "sql-evict-v3"}:
            sql_evict.add_argument("--registry-file", type=Path, required=True)
            sql_evict.add_argument("--registry-sha256", required=True)
    science_reconcile = sub.add_parser("science-scope-reconcile")
    science_reconcile.add_argument("--target-root", type=Path, required=True)
    science_reconcile.add_argument("--position", type=int, required=True)
    science_reconcile.add_argument("--source-binding-file", type=Path, required=True)
    science_reconcile.add_argument("--source-binding-sha256", required=True)
    science_reconcile.add_argument("--key-file", type=Path, required=True)
    science_reconcile.add_argument("--challenge-file", type=Path, required=True)
    science_reconcile.add_argument("--frame-file", type=Path, required=True)
    science_bootstrap = sub.add_parser("science-scope-bootstrap")
    science_bootstrap.add_argument("--new-root", type=Path, required=True)
    science_bootstrap.add_argument("--source-binding-file", type=Path, required=True)
    science_bootstrap.add_argument("--source-binding-sha256", required=True)
    science_bootstrap.add_argument("--metadata-dir", type=Path, required=True)
    science_bootstrap.add_argument("--capture-sha256", required=True)
    science_bootstrap.add_argument("--index-file", type=Path, required=True)
    science_bootstrap.add_argument("--index-sha256", required=True)
    science_bootstrap.add_argument("--challenge-file", type=Path, required=True)
    science_bootstrap.add_argument("--frame-file", type=Path, required=True)
    tick = sub.add_parser("tick")
    tick.add_argument("--schedule", type=Path, required=True)
    args = parser.parse_args()

    if args.operation == "status":
        from .ledger import Ledger
        from .wav_queue import summary

        ledger = Ledger(runtime.state_root())
        try:
            result = {
                "policy": POLICY_SHA256,
                "runtime": runtime.runtime_sha256(),
                "states": ledger.status(),
                "wav_backlog": summary(ledger.db),
            }
        finally:
            ledger.close()
    elif args.operation == "bindings":
        from app.raw_archive.runner import destination_from_environment

        from .storage import Store

        result = {"bindings_sha256": runtime.bindings_sha256(Store(destination_from_environment()))}
    elif args.operation == "snapshot-cycle":
        result = with_source_catalogue_lease(runtime.managed_snapshot)
    elif args.operation in {"snapshot-scope-activate", "snapshot-scope-reconcile"}:
        result = with_source_catalogue_lease(
            lambda: runtime.managed_snapshot_scope(
                activate=args.operation == "snapshot-scope-activate"
            )
        )
    elif args.operation in {
        "snapshot-pin-acquire",
        "snapshot-pin-release-frozen",
        "snapshot-pin-release-completed",
    }:
        result = with_source_catalogue_lease(
            lambda: runtime.managed_snapshot_pin(
                args.operation_file,
                args.operation_sha256,
                frozen_path=getattr(args, "frozen_file", None),
                frozen_sha256=getattr(args, "frozen_sha256", None),
                termination_bundle_path=getattr(args, "termination_bundle_file", None),
                termination_bundle_sha256=getattr(args, "termination_bundle_sha256", None),
                completed_pilot=args.operation == "snapshot-pin-release-completed",
            )
        )
    elif args.operation in {"restore-database", "restore-backup"}:
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
            if args.operation == "restore-backup":
                from .backup import key_from_file
                from .database_restore import restore_backup

                result = restore_backup(
                    args.folder,
                    url,
                    args.manifest_sha256,
                    key_from_file(args.key_file),
                    checkpoint=lambda: None,
                )
            else:
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
            result = with_source_catalogue_lease(
                lambda: runtime.backup(
                    args.source, backup_format=args.format, coordination_kind="catalog"
                ),
                job_kind="catalog",
            )
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
    elif args.operation == "sql-fence-plan":
        from .sql_runtime import fence_plan

        result = fence_plan(args.identity)
    elif args.operation == "sql-audit-plan":
        from .sql_deletion_audit import ddl_plan

        result = ddl_plan(args.writer_role)
    elif args.operation == "sql-audit-v3-plan":
        from .sql_audit_v3 import ddl_plan

        result = ddl_plan(args.writer_role, args.epoch, args.genesis_sha256)
    elif args.operation == "sql-scope-prepare-registry":
        from .execution import bounded_job
        from .sql_runtime import prepare_sql_registry

        def run_sql_registry():
            with bounded_job(runtime.state_root(), seconds=75, kind="sql-scope-registry"):
                return prepare_sql_registry()

        result = with_source_catalogue_lease(run_sql_registry)
    elif args.operation in {
        "sql-evict",
        "sql-evict-v3",
        "sql-source-approval-v3",
        "sql-scope-bootstrap",
        "sql-scope-reconcile",
        "sql-scope-bootstrap-v3",
        "sql-scope-reconcile-v3",
    }:
        from .execution import bounded_job
        from .sql_runtime import evict as sql_evict_explicit

        def run_sql_evict():
            with bounded_job(runtime.state_root(), seconds=75, kind="sql-evict"):
                extra = {}
                if args.operation == "sql-evict-v3":
                    extra = {"operation": "evict-v3"}
                elif args.operation == "sql-source-approval-v3":
                    extra = {
                        "operation": "source-approval-v3",
                        "binding_path": args.binding_file,
                        "binding_sha256": args.binding_sha256,
                        "dba_identity_path": args.dba_identity_file,
                        "dba_identity_sha256": args.dba_identity_sha256,
                        "producer_sha256": args.producer_sha256,
                        "challenge_file": args.challenge_file,
                        "frame_file": args.frame_file,
                    }
                elif args.operation != "sql-evict":
                    extra = {
                        "operation": args.operation.removeprefix("sql-"),
                        "registry_file": args.registry_file,
                        "registry_sha256": args.registry_sha256,
                    }
                return sql_evict_explicit(
                    args.identity,
                    restore_profile_file=args.restore_profile_file,
                    restore_profile_sha256=args.restore_profile_sha256,
                    proof_file=args.proof_file,
                    proof_sha256=args.proof_sha256,
                    authorization_file=args.authorization_file,
                    authorization_sha256=args.authorization_sha256,
                    snapshot_token_file=args.snapshot_token_file,
                    snapshot_token_sha256=args.snapshot_token_sha256,
                    **extra,
                )

        result = with_source_catalogue_lease(run_sql_evict)
    elif args.operation == "science-scope-reconcile":
        from .execution import bounded_job
        from .sql_runtime import science_reconcile

        def run_science_reconcile():
            with bounded_job(runtime.state_root(), seconds=75, kind="science-reconcile"):
                return science_reconcile(
                    args.target_root,
                    args.position,
                    source_binding_file=args.source_binding_file,
                    source_binding_sha256=args.source_binding_sha256,
                    key_file=args.key_file,
                    challenge_file=args.challenge_file,
                    frame_file=args.frame_file,
                )

        result = with_source_catalogue_lease(run_science_reconcile)
    elif args.operation == "science-scope-bootstrap":
        from .execution import bounded_job
        from .sql_runtime import science_bootstrap

        def run_science_bootstrap():
            with bounded_job(runtime.state_root(), seconds=75, kind="science-bootstrap"):
                return science_bootstrap(
                    args.new_root,
                    source_binding_file=args.source_binding_file,
                    source_binding_sha256=args.source_binding_sha256,
                    metadata_dir=args.metadata_dir,
                    capture_sha256=args.capture_sha256,
                    index_file=args.index_file,
                    index_sha256=args.index_sha256,
                    challenge_file=args.challenge_file,
                    frame_file=args.frame_file,
                )

        result = with_source_catalogue_lease(run_science_bootstrap)
    elif args.operation == "restore":
        from .recovery import restore

        with runtime.worker() as (ledger, store, checkpoint):
            result = restore(args.identity, args.output, ledger, store, checkpoint=checkpoint)
    else:
        from .execution import bounded_job
        from .schedule import tick

        def run_tick():
            with bounded_job(runtime.state_root()):
                return tick(args.schedule)

        result = with_source_catalogue_lease(run_tick)
    # Routine output contains aggregate counts/identities, never raw row bodies,
    # credentials, object keys, personal data or activation-file contents.
    if isinstance(result, dict) and {"files", "state", "candidate"} <= result.keys():
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
