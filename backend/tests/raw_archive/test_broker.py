"""Private socket restores verify bytes twice and expose no mutation operations."""

import threading
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from app.raw_archive import read_broker, reader
from app.raw_archive.policy import ArchiveBlocked


@pytest.fixture
def broker(world, monkeypatch, tmp_path):
    config, _, source, destination, add, execute = world
    record = add("gateway", 1)
    execute()
    scratch = tmp_path / "scratch"
    scratch.mkdir(mode=0o700)
    socket_dir = TemporaryDirectory(prefix="gm-read-", dir="/tmp")
    path = Path(socket_dir.name) / "read.sock"
    monkeypatch.setenv("RAW_ARCHIVE_READS_ENABLED", "true")
    monkeypatch.setenv("RAW_ARCHIVE_READ_BROKER_SOCKET", str(path))
    monkeypatch.setenv("RAW_ARCHIVE_READ_SCRATCH_DIR", str(scratch))
    monkeypatch.setattr(read_broker, "healthy", lambda: None)
    server = read_broker.Server(str(path), config, destination, scratch)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield record, source.data[record.key], destination, server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        socket_dir.cleanup()


def test_restore_without_public_ssh_credentials(broker, monkeypatch):
    record, payload, _, _ = broker
    monkeypatch.delenv("RAW_ARCHIVE_SFTP_KEY", raising=False)
    assert reader.verified_objects(record.kind, record.bucket, [record.key, "missing.wav"]) == {
        record.key
    }
    result = reader.restore_object(kind=record.kind, bucket=record.bucket, key=record.key)
    with result["Body"] as body:
        assert body.read() == payload
    assert result["ArchiveVerified"]


def test_corrupt_remote_rejected_before_stream(broker):
    record, _, destination, _ = broker
    destination.data[next(iter(destination.data))] = b"corrupted"
    with pytest.raises(ArchiveBlocked):
        reader.restore_object(kind=record.kind, bucket=record.bucket, key=record.key)


def test_unverified_key_and_concurrent_restore_rejected(broker):
    record, _, _, server = broker
    with pytest.raises(ArchiveBlocked):
        reader.restore_object(kind=record.kind, bucket=record.bucket, key="unknown.wav")
    server.read_slot.acquire()
    try:
        with pytest.raises(ArchiveBlocked):
            reader.restore_object(kind=record.kind, bucket=record.bucket, key=record.key)
    finally:
        server.read_slot.release()


@pytest.mark.parametrize(
    "data",
    [
        {"kind": "gateway", "bucket": "greenmind-raw", "key": "x.wav", "command": "put"},
        {"kind": "gateway", "bucket": "foreign", "key": "x.wav"},
        {"kind": "gateway", "bucket": "greenmind-raw", "key": "x\n.wav"},
        {"kind": "direct", "bucket": "greenmind-direct-production", "key": "x.wav"},
    ],
)
def test_reject_unknown_fields_sources_and_control_characters(data):
    with pytest.raises(ArchiveBlocked):
        read_broker.validate(data)


def test_no_write_method(broker):
    from app.raw_archive.broker_client import Connection

    connection = Connection()
    try:
        connection.request("PUT", "/read")
        assert connection.getresponse().status == 501
    finally:
        connection.close()
