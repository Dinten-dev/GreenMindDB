"""Bounded lossless SQL archives with independently checked row contents."""

import hashlib
import os
from datetime import datetime

from app.raw_archive.policy import ArchiveBlocked, checksum

from .codec import decode, encode, epoch_us

MAX_FILE_BYTES = 64 * 1024**2
MAX_DECODED_BYTES = 128 * 1024**2
MAX_ROWS = 100_000
ENVELOPE = ["identity", "organization", "sensor", "time_us", "body"]
STAT_TYPES = {
    "kind": "string",
    "unit": "string",
    "bucket_us": "int64",
    "seconds": "int32",
    "n": "int64",
    "total": "float64",
    "total2": "float64",
    "minimum": "float64",
    "maximum": "float64",
    "duration": "float64",
}


def statistics(body):
    original = body.get("row", {}) if isinstance(body, dict) else {}
    if not all(
        key in original
        for key in ("bucket", "seconds", "n", "total", "total2", "minimum", "maximum")
    ):
        return {"stat_" + key: None for key in STAT_TYPES}
    bucket = original["bucket"]
    return {
        "stat_kind": original.get(
            "kind",
            f"bio_signal_ch{original['channel'] + 1}" if "channel" in original else "bio_signal",
        ),
        "stat_unit": original.get("unit", "mV"),
        "stat_bucket_us": epoch_us(bucket)
        if isinstance(bucket, datetime)
        else int(bucket) * 1_000_000,
        **{
            "stat_" + key: original[key]
            for key in ("seconds", "n", "total", "total2", "minimum", "maximum")
        },
        "stat_duration": original.get("duration", 0.0),
    }


def write(path, rows, *, checkpoint=lambda: None):
    import pyarrow as pa
    import pyarrow.parquet as pq

    if path.exists():
        raise ArchiveBlocked("Archive destination already exists")
    schema = pa.schema(
        [
            ("identity", pa.string()),
            ("organization", pa.string()),
            ("sensor", pa.string()),
            ("time_us", pa.int64()),
            ("body", pa.binary()),
            *(("stat_" + name, getattr(pa, kind)()) for name, kind in STAT_TYPES.items()),
        ],
        metadata={b"greenmind_format": b"lossless-sql-v1"},
    )
    content = hashlib.sha256()
    count = decoded = 0
    previous = None
    batch = []
    with path.open("xb") as out, pq.ParquetWriter(out, schema, compression="zstd") as writer:
        os.chmod(path, 0o600)
        for row in rows:
            checkpoint()
            if set(row) != {"identity", "organization", "sensor", "time_us", "body"}:
                raise ArchiveBlocked("Incomplete archive row envelope")
            identity = (row["time_us"], row["identity"])
            if previous is not None and identity <= previous:
                raise ArchiveBlocked("Rows must be unique and deterministically ordered")
            previous = identity
            raw = encode(row["body"])
            envelope = encode(row)
            content.update(len(envelope).to_bytes(8, "big"))
            content.update(envelope)
            decoded += len(raw)
            count += 1
            if count > MAX_ROWS or decoded > MAX_DECODED_BYTES:
                raise ArchiveBlocked("SQL partition exceeds bounded archive budget")
            batch.append(dict(row, body=raw, **statistics(row["body"])))
            if len(batch) == 128:
                writer.write_table(pa.Table.from_pylist(batch, schema=schema), row_group_size=128)
                batch.clear()
                if out.tell() > MAX_FILE_BYTES:
                    raise ArchiveBlocked("Parquet archive exceeds transport budget")
        if batch:
            writer.write_table(pa.Table.from_pylist(batch, schema=schema), row_group_size=128)
    if not count or path.stat().st_size > MAX_FILE_BYTES:
        raise ArchiveBlocked("Empty or oversized archive")
    with path.open("rb") as persisted:
        os.fsync(persisted.fileno())
    return {
        "sha256": checksum(path),
        "bytes": path.stat().st_size,
        "rows": count,
        "content_sha256": content.hexdigest(),
        "decoded_bytes": decoded,
    }


def read(path, manifest, *, checkpoint=lambda: None):
    import pyarrow.parquet as pq

    if (
        path.stat().st_size != manifest["bytes"]
        or checksum(path) != manifest["sha256"]
        or not 0 < manifest["bytes"] <= MAX_FILE_BYTES
    ):
        raise ArchiveBlocked("Parquet archive checksum mismatch")
    source = pq.ParquetFile(path)
    if (source.schema_arrow.metadata or {}).get(b"greenmind_format") != b"lossless-sql-v1":
        raise ArchiveBlocked("Unsupported Parquet archive schema")
    if source.metadata.num_rows != manifest["rows"] or not 0 < manifest["rows"] <= MAX_ROWS:
        raise ArchiveBlocked("Parquet row count mismatch")
    if (
        sum(
            source.metadata.row_group(i).total_byte_size
            for i in range(source.metadata.num_row_groups)
        )
        > 2 * MAX_DECODED_BYTES
    ):
        raise ArchiveBlocked("Parquet decoding budget exceeded")
    content = hashlib.sha256()
    count = decoded = 0
    previous = None
    has_stats = all("stat_" + key in source.schema_arrow.names for key in STAT_TYPES)
    columns = ENVELOPE + (["stat_" + key for key in STAT_TYPES] if has_stats else [])
    for batch in source.iter_batches(batch_size=128, columns=columns):
        checkpoint()
        for row in batch.to_pylist():
            decoded += len(row["body"])
            if decoded > MAX_DECODED_BYTES:
                raise ArchiveBlocked("Parquet decoding budget exceeded")
            row["body"] = decode(row["body"])
            if has_stats:
                actual = {"stat_" + key: row.pop("stat_" + key) for key in STAT_TYPES}
                if actual != statistics(row["body"]):
                    raise ArchiveBlocked("Parquet statistics differ from original rows")
            identity = (row["time_us"], row["identity"])
            if previous is not None and identity <= previous:
                raise ArchiveBlocked("Parquet source identities overlap")
            previous = identity
            raw = encode(row)
            content.update(len(raw).to_bytes(8, "big"))
            content.update(raw)
            count += 1
            yield row
    if (
        content.hexdigest() != manifest["content_sha256"]
        or count != manifest["rows"]
        or decoded != manifest["decoded_bytes"]
    ):
        raise ArchiveBlocked("Parquet logical contents differ from source")


def verify(path, manifest, *, checkpoint=lambda: None):
    for _ in read(path, manifest, checkpoint=checkpoint):
        pass
