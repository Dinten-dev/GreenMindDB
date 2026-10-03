"""Consistent, bounded recovery backups; source databases and originals are read-only."""

import argparse
import base64
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from .policy import ArchiveBlocked, checksum

TABLES = {
    "gateway": (
        "organization",
        "users",
        "zone",
        "zone_access",
        "gateway",
        "sensor",
        "wav_file",
        "wav_feature",
        "visual_reading",
        "visual_wav",
        "visual_wave",
        "visual_chunk",
        "visual_worker",
    ),
    "direct": (
        "direct_device",
        "direct_session",
        "direct_segment",
        "direct_revision",
        "direct_visual_segment",
        "direct_visual_point",
        "direct_visual_worker",
    ),
}


def encode_value(value):
    if isinstance(value, bytes | memoryview):
        return {"__bytea__": base64.b64encode(value).decode("ascii")}
    return str(value)


def private_directory(path):
    if not path.is_absolute() or path.is_symlink():
        raise ArchiveBlocked("Absolute private recovery directory required")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ArchiveBlocked("Recovery directory is not private")
    if shutil.disk_usage(path).free < 2 * 1024**3:
        raise ArchiveBlocked("Recovery backup needs two GiB free disk")


def backup_ledger(source, output, *, checkpoint=lambda: None):
    """SQLite online backup includes committed WAL data; never copies a live main file."""
    if source.is_symlink() or output.exists() or not source.is_file():
        raise ArchiveBlocked("Backup requires an existing journal and a new output path")
    started = time.monotonic()
    pending = output.with_suffix(output.suffix + ".partial")
    descriptor = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    origin = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=2)
    target = sqlite3.connect(pending)
    try:
        origin.execute("PRAGMA query_only=ON")
        origin.execute("PRAGMA cache_size=-1024")
        target.execute("PRAGMA cache_size=-1024")

        def progress(_status, _remaining, _total):
            checkpoint()
            if time.monotonic() - started > 120 or pending.stat().st_size > 512 * 1024**2:
                raise ArchiveBlocked("Recovery journal backup exceeds its time/size budget")

        origin.backup(target, pages=100, progress=progress, sleep=0.01)
        if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ArchiveBlocked("Recovery journal integrity check failed")
        files = target.execute("SELECT count(*) FROM archive").fetchone()[0]
    finally:
        target.close()
        origin.close()
    with pending.open("rb") as body:
        os.fsync(body.fileno())
    os.link(pending, output)  # Never overwrite a previous backup.
    return {
        "file": output.name,
        "sha256": checksum(output),
        "bytes": output.stat().st_size,
        "rows": files,
    }


def backup_metadata(engine, kind, output, *, checkpoint=lambda: None):
    from sqlalchemy import text

    if output.exists() or kind not in TABLES:
        raise ArchiveBlocked("New output and an explicit metadata source are required")
    started, rows = time.monotonic(), 0
    with output.open("xb") as raw:
        os.chmod(output, 0o600)
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as body:
            with engine.connect().execution_options(isolation_level="REPEATABLE READ") as db:
                with db.begin():
                    if db.execute(text("SHOW transaction_read_only")).scalar() != "on":
                        raise ArchiveBlocked("Recovery database access must be read-only")
                    for table in TABLES[kind]:
                        result = (
                            db.execution_options(stream_results=True, yield_per=1000)
                            .execute(text('SELECT * FROM "' + table + '"'))
                            .mappings()
                        )
                        for row in result:
                            line = json.dumps(
                                {"table": table, "row": dict(row)},
                                default=encode_value,
                                sort_keys=True,
                                allow_nan=False,
                            )
                            body.write((line + "\n").encode())
                            rows += 1
                            if rows % 1000 == 0:
                                checkpoint()
                                if time.monotonic() - started > 120 or raw.tell() > 512 * 1024**2:
                                    raise ArchiveBlocked(
                                        "Metadata backup exceeds its time/size budget"
                                    )
        raw.flush()
        os.fsync(raw.fileno())
    # Full gzip/JSON decoding catches a truncated stream before reporting success.
    with gzip.open(output, "rb") as body:
        checked = sum(1 for line in body if json.loads(line))
    if checked != rows:
        raise ArchiveBlocked("Metadata backup row count differs after readback")
    return {
        "file": output.name,
        "sha256": checksum(output),
        "bytes": output.stat().st_size,
        "rows": rows,
        "tables": list(TABLES[kind]),
    }


def restore_ledger(source, digest, destination):
    """Restore into a new isolated directory; no existing journal is ever replaced."""
    if not destination.is_absolute() or destination.exists() or source.is_symlink():
        raise ArchiveBlocked("Restore requires a new absolute destination")
    if checksum(source) != digest:
        raise ArchiveBlocked("Recovery journal checksum mismatch")
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ArchiveBlocked("Recovery journal is corrupt")
        rows = db.execute("SELECT count(*) FROM archive").fetchone()[0]
    destination.mkdir(mode=0o700)
    target = destination / "archive.sqlite3"
    with source.open("rb") as original, target.open("xb") as restored:
        os.chmod(target, 0o600)
        shutil.copyfileobj(original, restored, 64 * 1024)
        restored.flush()
        os.fsync(restored.fileno())
    if checksum(target) != digest:
        raise ArchiveBlocked("Restored journal differs from backup")
    return rows


def publish_bundle(output, manifest, destination, namespace, *, checkpoint=lambda: None):
    """Additional catalog backups only; never sends or replaces any RAW WAV."""
    remote = dict(manifest)
    remote["environment"] = namespace
    remote["destination"] = destination.identity
    remote["files"] = []
    for entry in manifest["files"]:
        checkpoint()
        path = output / entry["file"]
        if checksum(path) != entry["sha256"]:
            raise ArchiveBlocked("Local recovery backup changed before publication")
        suffix = "jsonl.gz"
        if path.suffix == ".sqlite3":
            compressed = path.with_suffix(".sqlite3.gz")
            with path.open("rb") as body, compressed.open("xb") as raw:
                os.chmod(compressed, 0o600)
                with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as writer:
                    shutil.copyfileobj(body, writer, 64 * 1024)
                raw.flush()
                os.fsync(raw.fileno())
            path, suffix = compressed, "sqlite3.gz"
        digest = checksum(path)
        key = f"{namespace}/catalog-backup/{digest}.{suffix}"
        destination.publish_catalog(path, key)
        remote["files"].append(
            entry
            | {
                "remote_key": key,
                "transport_sha256": digest,
                "transport_bytes": path.stat().st_size,
            }
        )
    path = output / (manifest["created_at"].replace(":", "-") + "-offsite.json")
    with path.open("x") as body:
        os.chmod(path, 0o600)
        json.dump(remote, body, sort_keys=True, indent=2)
    key = f"{namespace}/catalog-backup/{checksum(path)}.json"
    checkpoint()
    destination.publish_catalog(path, key)
    return {"key": key, "sha256": checksum(path), "size": path.stat().st_size}


def restore_metadata(source, digest, engine):
    """Populate pre-migrated EMPTY test tables only; live databases are rejected."""
    from sqlalchemy import MetaData, Table, func, select

    database = engine.url.database or ""
    if not (
        database.startswith("greenmind_restore_")
        or engine.dialect.name == "sqlite"
        and database == ":memory:"
    ):
        raise ArchiveBlocked("Metadata restores require an isolated greenmind_restore_ database")
    if checksum(source) != digest:
        raise ArchiveBlocked("Metadata backup checksum mismatch")
    known, pending, count = {}, {}, 0
    permitted = {table for tables in TABLES.values() for table in tables}
    with engine.begin() as db:
        with gzip.open(source, "rb") as body:
            for line in body:
                entry = json.loads(line)
                name, values = entry["table"], entry["row"]
                if name not in permitted:
                    raise ArchiveBlocked("Unknown table in metadata backup")
                if name not in known:
                    for earlier, batch in pending.items():
                        if batch:
                            db.execute(known[earlier].insert(), batch)
                            batch.clear()
                    table = Table(name, MetaData(), autoload_with=db)
                    if db.execute(select(func.count()).select_from(table)).scalar():
                        raise ArchiveBlocked("Restore table is not empty")
                    known[name], pending[name] = table, []
                table = known[name]
                if set(values) != set(table.columns.keys()):
                    raise ArchiveBlocked("Recovery schema differs from migrated test schema")
                converted = {}
                for key, value in values.items():
                    column = table.columns[key]
                    if isinstance(value, dict) and column.type.python_type is bytes:
                        if set(value) != {"__bytea__"}:
                            raise ArchiveBlocked("Invalid binary recovery encoding")
                        value = base64.b64decode(value["__bytea__"], validate=True)
                    if value is not None and isinstance(value, str):
                        try:
                            expected = column.type.python_type
                        except NotImplementedError as error:
                            raise ArchiveBlocked("Unsupported recovery column type") from error
                        if expected is datetime:
                            value = datetime.fromisoformat(value)
                        elif expected.__name__ == "UUID":
                            value = expected(value)
                        elif expected.__name__ == "date":
                            value = expected.fromisoformat(value)
                        elif expected in (int, float) or expected.__name__ == "Decimal":
                            value = expected(value)
                        elif expected is bytes:
                            raise ArchiveBlocked("Binary metadata needs an explicit safe codec")
                    converted[key] = value
                pending[name].append(converted)
                count += 1
                if len(pending[name]) >= 1000:
                    db.execute(table.insert(), pending[name])
                    pending[name].clear()
        # Preserve the writer's parent-before-child table order.
        for name, rows in pending.items():
            if rows:
                db.execute(known[name].insert(), rows)
    return count


def recovery_index(recordings, ledger, namespace, destination):
    candidates = {}
    for record in recordings:
        state = ledger.load(record)
        if not state or state[0] != "verified":
            raise ArchiveBlocked("Recovery index requires verified archive receipts")
        candidates[record.archive_id] = record.content_identity() | {
            "remote_key": record.remote_key(namespace),
            "feature_digest": record.feature_digest,
            "source_snapshot": state[1]["snapshot"],
        }
    data = {
        "schema": 1,
        "environment": namespace,
        "destination": destination.identity,
        "candidates": candidates,
        "recordings": {
            record.archive_id: ledger.load(record)[1]["recording"] for record in recordings
        },
    }
    raw = json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if not candidates or len(raw) > 1024**2:
        raise ArchiveBlocked("Recovery index must contain a bounded reviewed set")
    return raw, hashlib.sha256(raw).hexdigest()


def restore_bundle(
    destination, reference, output, engines, namespace, *, snapshot=None, checkpoint=lambda: None
):
    """Read back and restore the entire offsite catalog into empty test databases."""
    private_directory(output)
    manifest_path = output / "offsite-manifest.json"
    destination.download_catalog(
        reference["key"], manifest_path, reference["size"], snapshot=snapshot
    )
    if (
        checksum(manifest_path) != reference["sha256"]
        or manifest_path.stat().st_size != reference["size"]
    ):
        raise ArchiveBlocked("Offsite catalog manifest readback mismatch")
    manifest = json.loads(manifest_path.read_text())
    if (
        manifest.get("environment") != namespace
        or manifest.get("destination") != destination.identity
    ):
        raise ArchiveBlocked("Offsite catalog belongs to another environment")
    entries = manifest.get("files", [])
    if len(entries) != 3 or set(engines) != {"gateway", "direct"}:
        raise ArchiveBlocked("Restore requires journal and both source catalogs")
    restored, kinds = [], set()
    for position, entry in enumerate(entries):
        checkpoint()
        transport = output / f"transport-{position}.gz"
        destination.download_catalog(
            entry["remote_key"], transport, entry["transport_bytes"], snapshot=snapshot
        )
        if (
            checksum(transport) != entry["transport_sha256"]
            or transport.stat().st_size != entry["transport_bytes"]
        ):
            raise ArchiveBlocked("Offsite catalog body readback mismatch")
        if entry["file"].endswith(".sqlite3"):
            kind = "ledger"
            source = output / "journal.sqlite3"
            written = 0
            with gzip.open(transport, "rb") as original, source.open("xb") as target:
                os.chmod(source, 0o600)
                while block := original.read(64 * 1024):
                    written += len(block)
                    if written > entry["bytes"] or written > 512 * 1024**2:
                        raise ArchiveBlocked("Recovery journal exceeds decompression budget")
                    target.write(block)
            rows = restore_ledger(source, entry["sha256"], output / "restored-journal")
        else:
            kind = "gateway" if entry["file"].endswith("-gateway.jsonl.gz") else "direct"
            rows = restore_metadata(transport, entry["sha256"], engines[kind])
        if kind in kinds or rows != entry["rows"]:
            raise ArchiveBlocked("Restored catalog kind or row count differs")
        kinds.add(kind)
        restored.append({"kind": kind, "rows": rows, "sha256": entry["sha256"]})
    if kinds != {"ledger", "gateway", "direct"}:
        raise ArchiveBlocked("Incomplete catalog restore")
    return {
        "schema": 1,
        "environment": namespace,
        "destination": destination.identity,
        "created_at": datetime.now(UTC).isoformat(),
        "catalog_manifest": reference,
        "restored": restored,
        "deleted_files": 0,
    }


def main():
    from sqlalchemy import create_engine

    from .health import SafetyPause
    from .runner import health_probe

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ledger", type=Path)
    parser.add_argument(
        "--restore-reference",
        type=Path,
        help="Read a catalog manifest reference and restore to pre-migrated empty test databases",
    )
    parser.add_argument("--snapshot", help="Read backup bodies from a named Storage Box snapshot")
    parser.add_argument(
        "--publish-catalog",
        action="store_true",
        help="Append verified catalog backups, never RAW objects",
    )
    args = parser.parse_args()
    # This command cannot invoke the copying or deletion runner.
    probe = health_probe()

    def checkpoint():
        if not probe():
            raise SafetyPause("Recovery backup paused to preserve receiver headroom")

    checkpoint()
    private_directory(args.output)
    if args.restore_reference:
        engines = {}
        try:
            for kind in TABLES:
                url = os.environ["RAW_ARCHIVE_RESTORE_" + kind.upper() + "_DATABASE_URL"]
                engine = create_engine(url, pool_size=1, max_overflow=0)
                if not (engine.url.database or "").startswith("greenmind_restore_"):
                    raise ArchiveBlocked("Explicit empty restoration databases are required")
                engines[kind] = engine
            from .runner import destination_from_environment

            destination = destination_from_environment()
            namespace = os.environ.get("RAW_ARCHIVE_ENVIRONMENT", "production")
            proof = restore_bundle(
                destination,
                json.loads(args.restore_reference.read_text()),
                args.output,
                engines,
                namespace,
                snapshot=args.snapshot,
                checkpoint=checkpoint,
            )
            raw = json.dumps(proof, sort_keys=True, indent=2).encode()
            digest = hashlib.sha256(raw).hexdigest()
            path = args.output / (digest + ".json")
            with path.open("xb") as body:
                os.chmod(path, 0o600)
                body.write(raw)
                body.flush()
                os.fsync(body.fileno())
            key = f"{namespace}/catalog-backup/{digest}.json"
            if args.publish_catalog:
                checkpoint()
                destination.publish_catalog(path, key)
            print(
                json.dumps(
                    {
                        "status": "restored",
                        "deleted_files": 0,
                        "proof": {"key": key, "sha256": digest, "size": len(raw)},
                        "published": args.publish_catalog,
                    }
                )
            )
            return
        finally:
            for engine in engines.values():
                engine.dispose()
    if args.ledger is None or args.snapshot:
        parser.error("Backup needs --ledger; --snapshot requires --restore-reference")
    identity = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    manifest = {"schema": 1, "created_at": datetime.now(UTC).isoformat(), "files": []}
    manifest["files"].append(
        backup_ledger(args.ledger, args.output / f"{identity}.sqlite3", checkpoint=checkpoint)
    )
    for kind in TABLES:
        url = os.environ["RAW_ARCHIVE_" + kind.upper() + "_DATABASE_URL"]
        engine = create_engine(
            url,
            pool_size=1,
            max_overflow=0,
            connect_args={
                "connect_timeout": 5,
                "options": "-c default_transaction_read_only=on "
                "-c statement_timeout=8000 -c lock_timeout=150 -c jit=off -c work_mem=8192",
            },
        )
        try:
            manifest["files"].append(
                backup_metadata(
                    engine, kind, args.output / f"{identity}-{kind}.jsonl.gz", checkpoint=checkpoint
                )
            )
        finally:
            engine.dispose()
    path = args.output / f"{identity}-manifest.json"
    with path.open("x") as body:
        os.chmod(path, 0o600)
        json.dump(manifest, body, indent=2)
        body.flush()
        os.fsync(body.fileno())
    result = {"status": "backed_up", "manifest": path.name, "deleted_files": 0}
    if args.publish_catalog:
        from .runner import destination_from_environment

        checkpoint()
        result["offsite_manifest"] = publish_bundle(
            args.output,
            manifest,
            destination_from_environment(),
            os.environ.get("RAW_ARCHIVE_ENVIRONMENT", "production"),
            checkpoint=checkpoint,
        )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
