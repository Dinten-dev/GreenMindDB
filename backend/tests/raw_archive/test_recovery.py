"""WAL-safe index backups and schema-aware recovery into empty test databases."""

import gzip
import json
import sqlite3
from datetime import datetime

import pytest
from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Integer,
    LargeBinary,
    MetaData,
    Table,
    create_engine,
    select,
)

from app.raw_archive.policy import ArchiveBlocked, checksum
from app.raw_archive.recovery import (
    backup_ledger,
    encode_value,
    publish_bundle,
    restore_bundle,
    restore_ledger,
    restore_metadata,
)


def test_online_backup_includes_wal_and_restores_without_overwriting(tmp_path):
    source, output = tmp_path / "live.sqlite3", tmp_path / "backup.sqlite3"
    live = sqlite3.connect(source)
    live.execute("PRAGMA journal_mode=WAL")
    live.execute("CREATE TABLE archive(id TEXT PRIMARY KEY,state TEXT,receipt TEXT)")
    live.execute("INSERT INTO archive VALUES ('1','verified','{}')")
    live.commit()
    receipt = backup_ledger(source, output)
    assert receipt["rows"] == 1
    assert restore_ledger(output, receipt["sha256"], tmp_path / "restored") == 1
    assert live.execute("SELECT count(*) FROM archive").fetchone()[0] == 1
    with pytest.raises(ArchiveBlocked, match="new absolute"):
        restore_ledger(output, receipt["sha256"], tmp_path / "restored")
    with pytest.raises(ArchiveBlocked, match="checksum"):
        restore_ledger(output, "a" * 64, tmp_path / "wrong")
    live.close()


def test_metadata_restore_preserves_types_and_refuses_existing_rows(tmp_path):
    engine = create_engine("sqlite:///:memory:")
    table = Table(
        "wav_file",
        MetaData(),
        Column("id", Integer, primary_key=True),
        Column("started_at", DateTime),
        Column("metadata", JSON),
        Column("binary", LargeBinary),
    )
    table.metadata.create_all(engine)
    source = tmp_path / "metadata.jsonl.gz"
    stamp = datetime(2026, 9, 20, 12)
    with gzip.open(source, "wt") as writer:
        writer.write(
            json.dumps(
                {
                    "table": "wav_file",
                    "row": {
                        "id": 1,
                        "started_at": stamp,
                        "metadata": {"scale": 0.1},
                        "binary": b"bytes",
                    },
                },
                default=encode_value,
            )
            + "\n"
        )
    assert restore_metadata(source, checksum(source), engine) == 1
    with engine.connect() as db:
        row = db.execute(select(table)).mappings().one()
        assert row["started_at"] == stamp and row["binary"] == b"bytes"
        assert row["metadata"] == {"scale": 0.1}
    with pytest.raises(ArchiveBlocked, match="not empty"):
        restore_metadata(source, checksum(source), engine)


def test_metadata_restore_refuses_live_database(tmp_path):
    engine = create_engine("sqlite:///production.sqlite3")
    with pytest.raises(ArchiveBlocked, match="isolated"):
        restore_metadata(tmp_path / "missing", "a" * 64, engine)


def test_offsite_bundle_reads_all_bytes_and_restores_both_catalogs(tmp_path):
    class Box:
        identity = "isolated-box"
        data = {}

        def publish_catalog(self, path, key):
            self.data[key] = path.read_bytes()

        def download_catalog(self, key, path, limit, *, snapshot=None):
            assert len(self.data[key]) <= limit
            path.write_bytes(self.data[key])

    box = Box()
    live = tmp_path / "live.sqlite3"
    db = sqlite3.connect(live)
    db.execute("CREATE TABLE archive(id TEXT PRIMARY KEY,state TEXT,receipt TEXT)")
    db.execute("INSERT INTO archive VALUES ('id','verified','{}')")
    db.commit()
    db.close()
    entry = backup_ledger(live, tmp_path / "copy.sqlite3")
    manifest = {"schema": 1, "created_at": "2026-10-03T12:00:00+00:00", "files": [entry]}
    engines = {}
    for kind, table_name in (("gateway", "wav_file"), ("direct", "direct_device")):
        path = tmp_path / ("copy-" + kind + ".jsonl.gz")
        with gzip.open(path, "wt") as body:
            body.write(
                json.dumps({"table": table_name, "row": {"id": 1, "metadata": {"unit": "mV"}}})
                + "\n"
            )
        manifest["files"].append(
            {"file": path.name, "sha256": checksum(path), "bytes": path.stat().st_size, "rows": 1}
        )
        engines[kind] = create_engine("sqlite:///:memory:")
        table = Table(
            table_name,
            MetaData(),
            Column("id", Integer, primary_key=True),
            Column("metadata", JSON),
        )
        table.metadata.create_all(engines[kind])
    reference = publish_bundle(tmp_path, manifest, box, "production")
    proof = restore_bundle(box, reference, tmp_path / "restore", engines, "production")
    assert {entry["kind"] for entry in proof["restored"]} == {"ledger", "gateway", "direct"}
    assert proof["deleted_files"] == 0 and all(entry["rows"] == 1 for entry in proof["restored"])
    # Same byte length does not bypass SHA-256 verification.
    box.data[reference["key"]] = b"x" * len(box.data[reference["key"]])
    with pytest.raises(ArchiveBlocked, match="readback mismatch"):
        restore_bundle(box, reference, tmp_path / "corrupt", engines, "production")
    for engine in engines.values():
        engine.dispose()
