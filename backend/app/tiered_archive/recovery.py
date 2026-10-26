"""Archive-only restoration into a new directory; originals are never replaced."""

import json
import os

from app.raw_archive.policy import ArchiveBlocked, checksum

from . import parquet
from .codec import encode
from .pipeline import candidate_json, verify_file
from .runtime import candidate_from_json


def restore(identity, output, ledger, store, *, checkpoint):
    receipt = ledger.get(identity)
    if not receipt or receipt["state"] not in {"verified", "deleting", "evicted"}:
        raise ArchiveBlocked("Verified archive receipt required for restoration")
    if not output.is_absolute() or output.exists() or output.is_symlink():
        raise ArchiveBlocked("Restore requires a new isolated directory")
    output.mkdir(mode=0o700, parents=False)
    candidate = candidate_from_json(receipt["source"])
    reference = receipt["manifest"]
    manifest = output / "manifest.json"
    store.download(reference["key"], manifest, reference["bytes"])
    verify_file(manifest, reference, checkpoint)
    data = json.loads(manifest.read_text())
    if (
        data["identity"] != identity
        or data["files"] != receipt["files"]
        or data["candidate"] != candidate_json(candidate)
    ):
        raise ArchiveBlocked("Restored bundle manifest mismatch")
    rows = 0
    for number, reference in enumerate(receipt["files"]):
        checkpoint()
        target = output / f"{number:04d}.{reference['format']}"
        store.download(reference["key"], target, reference["bytes"])
        os.chmod(target, 0o600)
        verify_file(target, reference, checkpoint)
        if reference["format"] == "parquet":
            with (output / f"{number:04d}.lossless.jsonl").open("xb") as body:
                os.chmod(body.name, 0o600)
                for row in parquet.read(target, reference, checkpoint=checkpoint):
                    body.write(encode(row) + b"\n")
                    rows += 1
                body.flush()
                os.fsync(body.fileno())
    result = {
        "identity": identity,
        "rows": rows,
        "files": len(receipt["files"]),
        "manifest_sha256": checksum(manifest),
        "source_accesses": 0,
    }
    report = output / "restore-result.json"
    report.write_text(json.dumps(result, sort_keys=True))
    os.chmod(report, 0o600)
    return result


def restore_database(folder, database_url, manifest_sha256, *, checkpoint):
    """Restore exact typed rows into an empty table in an isolated local DB."""
    from sqlalchemy import MetaData, Table, create_engine, func, select, text
    from sqlalchemy.engine import make_url

    from .codec import encode
    from .source import DATASETS

    url = make_url(database_url)
    if url.host not in {"localhost", "127.0.0.1", "::1"} or "_restore_" not in (url.database or ""):
        raise ArchiveBlocked("Only a dedicated local _restore_ database may be populated")
    manifest_path = folder / "manifest.json"
    if checksum(manifest_path) != manifest_sha256:
        raise ArchiveBlocked("Restore manifest changed")
    manifest = json.loads(manifest_path.read_text())
    candidate = candidate_from_json(manifest["candidate"])
    if candidate.dataset not in DATASETS:
        raise ArchiveBlocked("Use metadata/WAV restore for this bundle")
    spec = DATASETS[candidate.dataset]
    engine = create_engine(database_url, pool_size=1, max_overflow=0)
    count = 0
    try:
        with engine.begin() as db:
            db.execute(text("SET LOCAL lock_timeout='150ms'"))
            table = Table(spec.table, MetaData(), autoload_with=db)
            if db.execute(select(func.count()).select_from(table)).scalar_one() != 0:
                raise ArchiveBlocked("Restore never merges into a populated target table")
            for number, reference in enumerate(manifest["files"]):
                if reference["format"] != "parquet":
                    continue
                for envelope in parquet.read(
                    folder / f"{number:04d}.parquet", reference, checkpoint=checkpoint
                ):
                    body = envelope["body"]
                    if set(body) != {"row", "context"}:
                        raise ArchiveBlocked("Unexpected SQL restore envelope")
                    db.execute(table.insert().values(**body["row"]))
                    count += 1
                    if spec.table == "direct_chunk":
                        context = body["context"]
                        for name, rows in [
                            (
                                "direct_chunk_payload_ref",
                                [context["payload_reference"]]
                                if context["payload_reference"]
                                else [],
                            ),
                            ("direct_piece", context["pieces"]),
                        ]:
                            child = Table(name, MetaData(), autoload_with=db)
                            for row in rows:
                                db.execute(child.insert().values(**row))
            if count != candidate.rows:
                raise ArchiveBlocked("Database restoration row count mismatch")
            # Verify typed values after PostgreSQL has stored them, not merely
            # that INSERT accepted the input objects.
            keys = {}
            reference = manifest["files"][0]
            for envelope in parquet.read(folder / "0000.parquet", reference, checkpoint=checkpoint):
                keys[encode({key: envelope["body"]["row"][key] for key in spec.keys})] = encode(
                    envelope["body"]["row"]
                )
            for row in db.execute(select(table)).mappings():
                identity = encode({key: row[key] for key in spec.keys})
                if keys.pop(identity, None) != encode(dict(row)):
                    raise ArchiveBlocked("Stored PostgreSQL values differ from archive")
            if keys:
                raise ArchiveBlocked("Restored source identities missing")
        return {"restored_rows": count, "typed_values_verified": True, "source_accesses": 0}
    finally:
        engine.dispose()
