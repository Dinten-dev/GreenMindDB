"""Exact-set source-side pilot. Default: full rehearsal, zero S3 deletes.

The archive host keeps copying. A hash-pinned snapshot index carries only the
reviewed receipts to a separate deletion journal on the source host.
"""

import argparse
import json
import os
import tempfile
import time
from contextlib import ExitStack
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from .deletion import approval, proposal, verify_preparation_evidence
from .policy import ArchiveBlocked, Ledger, Recording, archive_one, checksum
from .worker import configuration, exclusive_worker


def load_index(config, destination, data):
    reference = data["recovery_index"]
    if type(reference.get("size")) is not int or not 0 < reference["size"] <= 1024**2:
        raise ArchiveBlocked("Bounded snapshot recovery index required")
    with tempfile.TemporaryDirectory(prefix="pilot-index-", dir=config.root) as folder:
        path = Path(folder) / "index.json"
        destination.download_recovery(
            reference["key"], path, reference["size"], snapshot=data["snapshot"]["name"]
        )
        if path.stat().st_size != reference["size"] or checksum(path) != reference["sha256"]:
            raise ArchiveBlocked("Snapshot pilot index readback mismatch")
        return json.loads(path.read_bytes())


def reviewed_records(config, data, index, destination):
    if (
        index.get("schema") != 1
        or index.get("environment") != config.namespace
        or index.get("destination") != destination.identity
        or index.get("candidates") != data["candidates"]
        or set(index.get("recordings", {})) != set(data["candidates"])
        or set(index.get("receipts", {})) != set(data["candidates"])
    ):
        raise ArchiveBlocked("Snapshot must contain the exact reviewed recordings and receipts")
    records = []
    for identity in sorted(data["candidates"]):
        record = Recording.from_receipt(index["recordings"][identity])
        receipt = index["receipts"][identity]
        expected = record.content_identity() | {
            "remote_key": record.remote_key(config.namespace),
            "feature_digest": record.feature_digest,
            "source_snapshot": receipt["snapshot"],
        }
        if (
            record.archive_id != identity
            or data["candidates"][identity] != expected
            or receipt.get("recording") != index["recordings"][identity]
            or receipt.get("destination") != destination.identity
            or receipt.get("remote_key") != expected["remote_key"]
            or not receipt.get("first_verified_at")
        ):
            raise ArchiveBlocked("Reviewed receipt identity or original grace clock changed")
        records.append(record)
    return records


def run_pilot(
    config,
    sources,
    catalogs,
    destination,
    *,
    healthy,
    execute=False,
    clock=lambda: datetime.now(UTC),
    budget_seconds=120,
    report=None,
):
    report = report if report is not None else {}
    report.update(
        status="blocked",
        deleted_files=0,
        completed_files=0,
        deletion_attempted_files=0,
        deletion_outcome_unknown=False,
        execute=execute,
    )
    if execute:
        config.guard(deleting=True)
        if os.environ.get("RAW_ARCHIVE_WORKER_ROLE") != "source":
            raise ArchiveBlocked("WAV deletion is restricted to the source-side pilot")
    elif config.delete_enabled:
        raise ArchiveBlocked("Rehearsal requires deletion disabled")
    if set(sources) != {"gateway", "direct"} or set(catalogs) != set(sources):
        raise ArchiveBlocked("Both strict source catalogs are required")
    config.guard()
    started = time.monotonic()

    def checkpoint():
        if time.monotonic() - started >= budget_seconds:
            raise ArchiveBlocked("Pilot reached its runtime budget")
        if (config.root / "PAUSE").exists() or not healthy():
            raise ArchiveBlocked("Pilot paused for receiver headroom")

    checkpoint()
    data = approval(config, clock()) if execute else proposal(config, clock())
    if (
        config.namespace == "production"
        and os.getenv("RAW_ARCHIVE_MANAGED_SCOPE_REQUIRED") == "true"
    ):
        from .snapshot_api import require_pilot_pin_receipt

        # The root controller acquires and durably backs up the archive pin first.
        # Any failure below deliberately leaves that pin active for reconciliation.
        require_pilot_pin_receipt(data, config.deletion_approval_sha256)
    config.root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with exclusive_worker(config):
        index = load_index(config, destination, data)
        records = reviewed_records(config, data, index, destination)
        report["reviewed_files"] = len(records)
        report["reviewed_bytes"] = sum(record.size for record in records)
        # Validate the WHOLE reviewed set before starting removal of its first file.
        for record in records:
            checkpoint()
            record.eligible(clock(), config)
            catalogs[record.kind].revalidate(record)
            receipt = index["receipts"][record.archive_id]
            from contextlib import nullcontext

            from .worklease import exclusive_wav_lease

            fence = (
                exclusive_wav_lease(
                    "direct",
                    record.bucket,
                    record.key,
                    source_engine=catalogs["direct"].sessions.kw["bind"],
                )
                if record.kind == "direct"
                else nullcontext()
            )
            with fence:
                verify_preparation_evidence(
                    config,
                    record,
                    receipt,
                    destination,
                    clock(),
                    clock=clock,
                    scientific_source=getattr(catalogs[record.kind], "scientific_history", None),
                )
            with tempfile.TemporaryDirectory(prefix="pilot-read-", dir=config.root) as folder:
                path = Path(folder) / "current.wav"
                destination.download(receipt["remote_key"], path, record.size)
                if path.stat().st_size != record.size or checksum(path) != record.sha256:
                    raise ArchiveBlocked("Current Storage Box WAV readback mismatch")
            target = sources[record.kind].deletion_target(record, receipt["snapshot"])
            if target is None:
                # Missing sources are permitted only after an already-durable attempt.
                import sqlite3

                journal_path = config.root / "archive.sqlite3"
                if not execute or not journal_path.is_file():
                    raise ArchiveBlocked("Reviewed source is absent without a deletion receipt")
                with sqlite3.connect(journal_path.as_uri() + "?mode=ro", uri=True) as db:
                    row = db.execute(
                        "SELECT state,receipt FROM archive WHERE id=?", (record.archive_id,)
                    ).fetchone()
                if (
                    not row
                    or row[0] not in {"deleting", "evicted"}
                    or (json.loads(row[1])["snapshot"] != receipt["snapshot"])
                ):
                    raise ArchiveBlocked("Source absence lacks the exact durable deletion receipt")
        checkpoint()
        (approval if execute else proposal)(config, clock())
        if not execute:
            report["status"] = "READY_FOR_PILOT_APPROVAL"
            return report
        journal = Ledger(config.root)
        try:
            for record in records:
                checkpoint()
                existing = journal.load(record)
                if existing is None:
                    journal.save(record, "verified", index["receipts"][record.archive_id])
                elif any(
                    existing[1].get(key) != index["receipts"][record.archive_id].get(key)
                    for key in ("snapshot", "remote_key", "destination", "first_verified_at")
                ):
                    raise ArchiveBlocked("Isolated deletion journal belongs to another pilot")
                was_evicted = existing and existing[0] == "evicted"
                report["deletion_attempted_files"] += int(not was_evicted)
                report["deletion_outcome_unknown"] = True
                status = archive_one(
                    config,
                    record,
                    sources[record.kind],
                    destination,
                    journal,
                    catalogs[record.kind].revalidate,
                    checkpoint=checkpoint,
                    now=clock,
                )
                report["completed_files"] += int(status == "evicted")
                report["deleted_files"] += int(status == "evicted" and not was_evicted)
                report["deletion_outcome_unknown"] = False
            report["status"] = "PILOT_COMPLETE"
        finally:
            journal.close()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument(
        "--state-dir",
        type=Path,
        required=True,
        help="Separate private pilot journal, never the copying journal",
    )
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    config = configuration()
    if (
        not args.state_dir.is_absolute()
        or args.state_dir.is_symlink()
        or args.state_dir.resolve() == config.root.resolve()
    ):
        parser.error("--state-dir must be separate from the configured copy journal")
    config = replace(
        config,
        root=args.state_dir,
        deletion_approval_file=args.manifest,
        deletion_approval_sha256=args.sha256,
    )
    if not args.execute:
        config = replace(config, enabled=True, delete_enabled=False, reads_accepted=False)
    report = {"deleted_files": 0, "status": "blocked"}
    try:
        if args.execute:
            config.guard(deleting=True)
            if os.environ.get("RAW_ARCHIVE_WORKER_ROLE") != "source":
                raise ArchiveBlocked("WAV deletion is restricted to the source-side pilot")
        # Reject bad/expired manifests before reading keys or making connections.
        (approval if args.execute else proposal)(config, datetime.now(UTC))
        with ExitStack() as cleanup:
            from .pilot_runtime import adapters

            sources, catalogs, destination, healthy = adapters(config, cleanup)
            run_pilot(
                config,
                sources,
                catalogs,
                destination,
                healthy=healthy,
                execute=args.execute,
                report=report,
            )
    except Exception as error:
        report["status"] = "blocked"
        report["error"] = type(error).__name__
        if isinstance(error, ArchiveBlocked):
            report["reason"] = str(error)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] in {"READY_FOR_PILOT_APPROVAL", "PILOT_COMPLETE"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
