"""Original URLs read immutable catalogue snapshots without writer permissions."""

import hashlib
import os
import sqlite3
import threading
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from botocore.exceptions import ClientError

from app.raw_archive import read_broker, reader
from app.raw_archive.policy import ArchiveBlocked
from app.raw_archive.read_catalog import connect, publish
from app.tiered_archive.read_service import ReadHandler


@pytest.fixture
def read_view(world, tmp_path, monkeypatch):
    config, _, source, destination, add, execute = world
    records = [add("gateway", 1), add("direct", 2)]
    execute()
    state = tmp_path / "readable"
    state.mkdir()
    db = sqlite3.connect(config.root / "archive.sqlite3")
    publish(db, state, os.getgid())
    monkeypatch.setenv("RAW_ARCHIVE_READ_CATALOG_FILE", str(state / "raw-read.sqlite3"))
    yield config, source, destination, records, db, state
    db.close()


def test_read_view_needs_no_access_to_writer_directory_and_cannot_write(read_view):
    config, _, _, _, _, state = read_view
    missing_writer = replace(config, root=state / "never-created-writer-directory")
    before = (state / "raw-read.sqlite3").read_bytes()
    with connect(missing_writer) as db:
        assert db.execute("PRAGMA query_only").fetchone() == (1,)
        assert db.execute("SELECT COUNT(*) FROM archive").fetchone() == (2,)
        with pytest.raises(sqlite3.OperationalError):
            db.execute("DELETE FROM archive")
    assert not missing_writer.root.exists()
    assert (state / "raw-read.sqlite3").read_bytes() == before
    assert not list(state.glob("*-wal")) and not list(state.glob("*-shm"))
    assert (state / "raw-read.sqlite3").stat().st_mode & 0o777 == 0o640


def test_reader_pins_old_snapshot_while_new_readers_get_complete_replacement(read_view):
    config, _, _, _, writer, state = read_view
    with connect(config) as previous:
        assert previous.execute("SELECT COUNT(*) FROM archive").fetchone() == (2,)
        writer.execute("INSERT INTO archive VALUES('artificial','published','{}')")
        writer.commit()
        publish(writer, state, os.getgid())
        assert previous.execute("SELECT COUNT(*) FROM archive").fetchone() == (2,)
        with connect(config) as current:
            assert current.execute("SELECT COUNT(*) FROM archive").fetchone() == (3,)


def test_interrupted_backup_preserves_previous_view_and_removes_only_own_temporary_file(read_view):
    config, _, _, _, writer, state = read_view
    before = (state / "raw-read.sqlite3").read_bytes()
    calls = []

    def checkpoint():
        calls.append(1)
        if len(calls) == 2:
            raise ArchiveBlocked("synthetic resource pause during backup")

    with pytest.raises(ArchiveBlocked, match="synthetic resource pause"):
        publish(writer, state, os.getgid(), checkpoint=checkpoint)
    assert (state / "raw-read.sqlite3").read_bytes() == before
    assert not list(state.glob("raw-read-view-*"))
    with connect(config) as db:
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)


def test_read_catalogue_symlinks_never_resolve_to_other_state(read_view, monkeypatch):
    config, _, _, _, writer, state = read_view
    real = state / "raw-read.sqlite3"
    alias = state / "alias.sqlite3"
    alias.symlink_to(real)
    monkeypatch.setenv("RAW_ARCHIVE_READ_CATALOG_FILE", str(alias))
    with pytest.raises(ArchiveBlocked, match="Private absolute"):
        with connect(config):
            pytest.fail("Symlink must not open")
    real.rename(state / "previous.sqlite3")
    real.symlink_to(state / "previous.sqlite3")
    with pytest.raises(ArchiveBlocked, match="symlink"):
        publish(writer, state, os.getgid())


@pytest.fixture
def original_url_broker(read_view, monkeypatch):
    config, source, destination, records, _, state = read_view
    monkeypatch.setenv("RAW_ARCHIVE_READS_ENABLED", "true")
    monkeypatch.setenv("TIERED_ARCHIVE_RAW_READS_ENABLED", "true")
    monkeypatch.setenv("TIERED_ARCHIVE_STATE_DIR", str(state))
    monkeypatch.setenv("RAW_ARCHIVE_DIRECT_S3_BUCKET", records[1].bucket)
    with TemporaryDirectory(prefix="gm-wav-view-", dir="/tmp") as folder:
        path = Path(folder) / "read.sock"
        monkeypatch.setenv("RAW_ARCHIVE_READ_BROKER_SOCKET", str(path))
        missing_writer = replace(config, root=state / "not-a-readable-writer-directory")
        with read_broker.Server(
            str(path), missing_writer, destination, state, health_check=lambda: None
        ) as server:
            server.RequestHandlerClass = ReadHandler
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
            thread.start()
            try:
                yield source, destination, records, state
            finally:
                server.shutdown()
                thread.join(timeout=5)


@pytest.mark.parametrize("number", [0, 1])
def test_original_s3_miss_falls_back_over_private_socket_and_read_only_view(
    original_url_broker, number
):
    source, _, records, _ = original_url_broker
    record = records[number]

    class MissingOriginal:
        def get_object(self, **kwargs):
            assert kwargs == {"Bucket": record.bucket, "Key": record.key}
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")

    assert reader.verified_objects(record.kind, record.bucket, [record.key, "missing.wav"]) == {
        record.key
    }
    response = reader.get_object(
        MissingOriginal(), Bucket=record.bucket, Key=record.key, kind=record.kind
    )
    with response["Body"] as body:
        payload = body.read()
    assert response["ArchiveVerified"]
    assert payload == source.data[record.key]
    assert hashlib.sha256(payload).hexdigest() == record.sha256


def test_changed_archive_blocks_original_url_before_unverified_bytes_leave_broker(
    original_url_broker,
):
    source, destination, records, state = original_url_broker
    record = records[0]
    before = dict(source.data)
    remote = record.remote_key("production")
    destination.data[remote] = b"x" * record.size
    with pytest.raises(ArchiveBlocked):
        reader.restore_object(kind=record.kind, bucket=record.bucket, key=record.key)
    assert source.data == before
    assert not list(state.glob("restore-*"))
