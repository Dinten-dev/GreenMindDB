"""Complete fresh WAV-only stages, independently restore, then reconcile quarantine.

This cannot migrate Staging, publish a reader, accept snapshots or authorize deletion.
"""

import argparse
import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import archive_host_catalog, catalog_stages, readiness, wav_catalog, wav_reconcile
from .health import SafetyPause
from .policy import ArchiveBlocked, checksum


def private_json(path):
    if (
        path.is_symlink()
        or not path.is_file()
        or path.stat().st_uid != os.geteuid()
        or path.stat().st_mode & 0o077
        or path.stat().st_size > wav_catalog.ROW_BYTES
    ):
        raise ArchiveBlocked("Private bounded completed evidence required")
    return json.loads(path.read_text())


def finalize(stages, quarantine, output, *, now=None):
    archive_host_catalog.configured()
    now = now or datetime.now(UTC)
    if output.exists() or set(stages) != set(catalog_stages.KINDS):
        raise ArchiveBlocked("All three stages and a new private result are required")
    exclusion = private_json(quarantine)
    if exclusion.get("schema") != 1 or exclusion.get("environment") != "production":
        raise ArchiveBlocked("Exact production quarantine required")
    excluded = [(entry["kind"], entry["identity"]) for entry in exclusion["excluded"]]
    completed = {kind: catalog_stages.load_stage(Path(path), kind) for kind, path in stages.items()}
    for value in completed.values():
        begin, end = (datetime.fromisoformat(value[k]) for k in ("started_at", "finished_at"))
        if not now - timedelta(hours=24) <= begin <= end <= now:
            raise ArchiveBlocked("Catalog source checkpoints must be fresh and not future dated")
    files = [part for stage in completed.values() for part in stage["files"]]
    if (
        sum(p["bytes"] for p in files) > 256 * 1024**2
        or sum(p["decoded_bytes"] for p in files) > 512 * 1024**2
    ):
        raise SafetyPause("catalog_spool_budget")
    probe = archive_host_catalog.health_probe()
    started = time.monotonic()

    def checkpoint():
        if time.monotonic() - started >= 110:
            raise SafetyPause("catalog_local_phase_deadline")
        if not probe():
            raise SafetyPause(probe.last.get("reason", "headroom"), probe.last)

    try:
        checkpoint()
        wav_catalog.private_directory(output)
        catalog_stages.assemble(stages, output / "bundle", checkpoint=checkpoint, copy_parts=True)
        manifest_path = output / "bundle/manifest.json"
        # This function verifies independent subaccount readback into a new database.
        proof = archive_host_catalog.publish_restore(manifest_path, output / "readback")
        manifest = json.loads(manifest_path.read_text())
        readiness.validate_metadata_restore(manifest, proof)
        started = time.monotonic()
        reconciled = wav_reconcile.reconcile(
            manifest_path,
            output / "reconciliation",
            excluded=excluded,
            checkpoint=checkpoint,
        )
        checkpoint()
        wav_catalog.save_json(output / "reconciliation/reconciliation.json", reconciled)
        result = {
            "status": "catalog_restored_and_reconciled",
            "complete_catalog": True,
            "independent_restoration": True,
            "manifest_sha256": checksum(manifest_path),
            "quarantine_sha256": checksum(quarantine),
            "catalog_manifest": proof["catalog_manifest"],
            "tables": proof["tables"],
            "reconciliation": reconciled,
            "cross_database_atomic": False,
            "remote_full_cohort_readback": False,
            "reader_authorization_accepted": False,
            "provider_snapshot_accepted": False,
            "deletion_readiness": False,
            "deleted_files": 0,
        }
        wav_catalog.save_json(output / "verified.json", result)
        return result
    finally:
        probe.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for kind in catalog_stages.KINDS:
        parser.add_argument("--" + kind + "-stage", type=Path, required=True)
    parser.add_argument("--quarantine", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(
            json.dumps(
                finalize(
                    {kind: getattr(args, kind + "_stage") for kind in catalog_stages.KINDS},
                    args.quarantine,
                    args.output,
                )
            )
        )
        return 0
    except SafetyPause as error:
        print(json.dumps({"status": "paused", "reason": error.code, "deleted_files": 0}))
        return 75
    except Exception as error:
        print(
            json.dumps(
                {"status": "blocked", "error_type": type(error).__name__, "deleted_files": 0}
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
