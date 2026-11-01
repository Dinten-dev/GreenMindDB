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
    from sqlalchemy import MetaData, Table, and_, create_engine, func, select, text

    from .source import DATASETS

    url = restore_url(database_url)
    manifest_path = folder / "manifest.json"
    if checksum(manifest_path) != manifest_sha256:
        raise ArchiveBlocked("Restore manifest changed")
    manifest = json.loads(manifest_path.read_text())
    candidate = candidate_from_json(manifest["candidate"])
    if candidate.dataset not in DATASETS:
        raise ArchiveBlocked("Use metadata/WAV restore for this bundle")
    spec = DATASETS[candidate.dataset]
    if candidate.source != spec.source or manifest.get("identity") != candidate.identity:
        raise ArchiveBlocked("Restore source identity mismatch")
    parts = manifest["files"]
    if (
        not 1 <= len(parts) <= 128
        or any(part["format"] != "parquet" for part in parts)
        or sum(part["rows"] for part in parts) != candidate.rows
        or sum(part["decoded_bytes"] for part in parts) > parquet.MAX_DECODED_BYTES
    ):
        raise ArchiveBlocked("Complete bounded SQL restore parts required")

    def rows():
        for number, reference in enumerate(parts):
            for envelope in parquet.read(
                folder / f"{number:04d}.parquet", reference, checkpoint=checkpoint
            ):
                if envelope["organization"] != candidate.organization:
                    raise ArchiveBlocked("SQL restore organization mismatch")
                if set(envelope["body"]) != {"row", "context"}:
                    raise ArchiveBlocked("Unexpected SQL restore envelope")
                checkpoint()
                yield envelope["body"]

    checkpoint()
    engine = create_engine(url, pool_size=1, max_overflow=0)
    count = 0
    try:
        with engine.begin() as db:
            db.execute(text("SET LOCAL lock_timeout='150ms'"))
            db.execute(text("SET LOCAL statement_timeout='8000ms'"))
            metadata = MetaData()
            table = Table(spec.table, metadata, autoload_with=db)
            children = {}
            if spec.table == "direct_chunk":
                children = {
                    name: Table(name, metadata, autoload_with=db)
                    for name in ("direct_chunk_payload_ref", "direct_piece")
                }
            # Fixed allowlisted names only. Keep concurrent writers out until
            # the final typed readback and transaction commit have completed.
            for target in (table, *children.values()):
                db.execute(text(f'LOCK TABLE "{target.name}" IN ACCESS EXCLUSIVE MODE'))
                if db.execute(select(func.count()).select_from(target)).scalar_one() != 0:
                    raise ArchiveBlocked("Restore never merges into a populated target table")
            child_counts = dict.fromkeys(children, 0)
            for body in rows():
                db.execute(table.insert().values(**body["row"]))
                count += 1
                if children:
                    for name, values in direct_children(body).items():
                        for row in values:
                            db.execute(children[name].insert().values(**row))
                            child_counts[name] += 1
            if count != candidate.rows:
                raise ArchiveBlocked("Database restoration row count mismatch")
            if db.execute(select(func.count()).select_from(table)).scalar_one() != count:
                raise ArchiveBlocked("Stored PostgreSQL row count differs from archive")
            for name, child in children.items():
                if (
                    db.execute(select(func.count()).select_from(child)).scalar_one()
                    != child_counts[name]
                ):
                    raise ArchiveBlocked("Stored Direct dependency count differs from archive")
            # Re-read every part without retaining all payloads in RAM. Check
            # the database values, including changes made by database triggers.
            for body in rows():
                expected = body["row"]
                actual = (
                    db.execute(
                        select(table).where(
                            and_(*(table.c[key] == expected[key] for key in spec.keys))
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if actual is None or encode(dict(actual)) != encode(expected):
                    raise ArchiveBlocked("Stored PostgreSQL values differ from archive")
                if children:
                    for name, expected_rows in direct_children(body).items():
                        actual_rows = db.execute(
                            select(children[name]).where(
                                children[name].c.chunk_id == expected["id"]
                            )
                        ).mappings()
                        if sorted(encode(dict(row)) for row in actual_rows) != sorted(
                            encode(row) for row in expected_rows
                        ):
                            raise ArchiveBlocked("Stored Direct dependencies differ from archive")
        return {"restored_rows": count, "typed_values_verified": True, "source_accesses": 0}
    finally:
        engine.dispose()


def restore_url(database_url):
    """Reject libpq host/service overrides before creating any connection."""
    import re

    from sqlalchemy.engine import make_url

    url = make_url(database_url)
    if (
        url.drivername not in {"postgresql", "postgresql+psycopg2"}
        or url.host not in {"localhost", "127.0.0.1", "::1"}
        or not re.fullmatch(r"[a-z][a-z0-9_]*_restore_[a-z0-9_]+", url.database or "")
        or not url.username
        or url.query
    ):
        raise ArchiveBlocked(
            "Only a dedicated local _restore_ database without overrides may be populated"
        )
    return url


def archived_direct_original(body):
    """Verify and return the exact chunk bytes stored in an archive context."""
    import hashlib

    row, context = body["row"], body["context"]
    if not isinstance(context, dict) or not isinstance(context.get("original_payload"), bytes):
        raise ArchiveBlocked("Archived Direct original bytes are missing")
    payload = context["original_payload"]
    if (
        type(row.get("payload_bytes")) is not int
        or len(payload) != row["payload_bytes"]
        or not isinstance(row.get("payload_sha256"), str)
        or hashlib.sha256(payload).hexdigest() != row["payload_sha256"]
        or (row.get("payload") is not None and bytes(row["payload"]) != payload)
    ):
        raise ArchiveBlocked("Archived Direct original bytes differ from source identity")
    from app.direct.capture_assignment import split
    from app.direct.protocol import parse_metadata

    try:
        config, _ = split(context["session_config"])
        metadata = parse_metadata(
            config
            | {
                "sequence": row["sequence"],
                "first_frame": row["first_frame"],
                "frame_count": row["frame_count"],
                "payload_sha256": row["payload_sha256"],
            }
        )
        if (
            metadata.digest() != row["digest"]
            or len(payload) != metadata.frame_count * metadata.channels * metadata.sample_bits // 8
        ):
            raise ValueError("original protocol identity differs")
        pieces = sorted(context["pieces"], key=lambda piece: piece["payload_offset"])
        expected = list(metadata.pieces())
        if len(pieces) != len(expected) or any(
            piece["chunk_id"] != row["id"]
            or (piece["payload_offset"], piece["frame_count"]) != (bound[3], bound[4])
            for piece, bound in zip(pieces, expected, strict=True)
        ):
            raise ValueError("archived original piece coverage differs")
    except (KeyError, TypeError, ValueError) as error:
        raise ArchiveBlocked("Archived Direct original protocol or pieces differ") from error
    return payload


def direct_children(body):
    context = body["context"]
    # Older test/legacy rows may lack the new context; real source rows with a
    # payload checksum require the independently verified archived original.
    if "payload_sha256" in body["row"] or "original_payload" in context:
        archived_direct_original(body)
    result = {
        "direct_chunk_payload_ref": [context["payload_reference"]]
        if context["payload_reference"] is not None
        else [],
        "direct_piece": context["pieces"],
    }
    if any(row["chunk_id"] != body["row"]["id"] for values in result.values() for row in values):
        raise ArchiveBlocked("Direct dependency belongs to another chunk")
    return result
