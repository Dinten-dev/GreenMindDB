"""Existing raw download consumers continue to work after verified source offload."""

import io
import threading
import zipfile

import pytest
from botocore.exceptions import ClientError

from app.direct.config import DirectSettings
from app.direct.storage import ArtifactStore
from app.raw_archive import reader, runner, worker
from app.raw_archive.policy import ArchiveBlocked
from app.services import wav_service


class MissingS3:
    code = "NoSuchKey"

    def get_object(self, **kwargs):
        raise ClientError({"Error": {"Code": self.code}}, "GetObject")

    def download_fileobj(self, *args):
        raise ClientError({"Error": {"Code": self.code}}, "HeadObject")


def prepare(world, monkeypatch, kind="gateway"):
    config, _, source, destination, add, execute = world
    record = add(kind, 1)
    execute()
    monkeypatch.setenv("RAW_ARCHIVE_READS_ENABLED", "true")
    monkeypatch.setattr(worker, "configuration", lambda: config)
    monkeypatch.setattr(runner, "destination_from_environment", lambda: destination)
    monkeypatch.setattr(wav_service, "_get_s3_client", MissingS3)
    return record, source.data[record.key]


def test_gateway_download_extraction_and_zip_use_verified_archive(world, monkeypatch):
    record, payload = prepare(world, monkeypatch)
    assert b"".join(wav_service.stream_wav_bytes(record.key)) == payload
    output = io.BytesIO(b"old-content")
    wav_service.download_object(record.key, output)
    assert output.read() == payload
    bundle = b"".join(wav_service.stream_wav_zip([record.key], ["plant.wav"]))
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        assert archive.namelist() == ["plant.wav"]
        assert archive.read("plant.wav") == payload


def test_disabled_and_permission_failure_never_use_archive(world, monkeypatch):
    record, _ = prepare(world, monkeypatch)
    monkeypatch.setenv("RAW_ARCHIVE_READS_ENABLED", "false")
    with pytest.raises(ClientError):
        b"".join(wav_service.stream_wav_bytes(record.key))
    monkeypatch.setenv("RAW_ARCHIVE_READS_ENABLED", "true")
    client = MissingS3()
    client.code = "AccessDenied"
    with pytest.raises(ClientError):
        reader.get_object(client, Bucket=record.bucket, Key=record.key, kind=record.kind)


def test_corrupt_archive_does_not_become_silent_empty_zip(world, monkeypatch):
    record, _ = prepare(world, monkeypatch)
    destination = world[3]
    destination.data[next(iter(destination.data))] = b"corrupt"
    with pytest.raises(ArchiveBlocked, match="readback mismatch"):
        b"".join(wav_service.stream_wav_bytes(record.key))
    with pytest.raises(RuntimeError, match="bundle incomplete"):
        b"".join(wav_service.stream_wav_zip([record.key], ["plant.wav"]))


def test_archive_stream_closes_temporary_file_and_limits_parallel_reads(world, monkeypatch):
    record, _ = prepare(world, monkeypatch)
    first = reader.restore_object(kind=record.kind, bucket=record.bucket, key=record.key)
    second = reader.restore_object(kind=record.kind, bucket=record.bucket, key=record.key)
    try:
        with pytest.raises(ArchiveBlocked, match="concurrency"):
            reader.restore_object(kind=record.kind, bucket=record.bucket, key=record.key)
    finally:
        first["Body"].close()
        second["Body"].close()
    assert first["Body"].file.closed
    again = reader.restore_object(kind=record.kind, bucket=record.bucket, key=record.key)
    again["Body"].close()


def test_direct_download_uses_same_verified_fallback(world, monkeypatch, tmp_path):
    config, catalogs, source, destination, add, execute = world
    record = add("direct", 1)
    from dataclasses import replace

    key = "direct/" + "a" * 36 + "/" + "b" * 36 + "/" + "c" * 64 + ".wav"
    updated = replace(record, key=key)
    catalogs["direct"].records["0001"] = [updated]
    source.data[key] = source.data.pop(record.key)
    execute()
    monkeypatch.setenv("RAW_ARCHIVE_READS_ENABLED", "true")
    monkeypatch.setattr(worker, "configuration", lambda: config)
    monkeypatch.setattr(runner, "destination_from_environment", lambda: destination)
    store = ArtifactStore(DirectSettings(artifact_root=tmp_path, s3_bucket=updated.bucket))
    store.client = MissingS3()
    assert store.get(key) == source.data[key]


def test_aborted_zip_consumer_does_not_leave_queue_writer_running(monkeypatch):
    bodies = []

    class Source:
        def get_object(self, **kwargs):
            body = io.BytesIO(b"data" * 1024)
            bodies.append(body)
            return {"Body": body}

    monkeypatch.setattr(wav_service, "_get_s3_client", Source)
    stream = wav_service.stream_wav_zip(
        [f"{n}.wav" for n in range(100)], [f"{n}.wav" for n in range(100)]
    )
    next(stream)
    stream.close()
    assert not any(t.name == "greenmind-wav-zip" for t in threading.enumerate())
    assert all(body.closed for body in bodies)


def test_ungranted_zone_cannot_trigger_archive_access(client, db, setup_test_data, monkeypatch):
    from datetime import UTC, datetime
    from uuid import uuid4

    from app.auth import create_access_token
    from app.models.user import Role, User
    from app.models.wav_file import WavFile

    member = User(
        email="no-archive-zone@example.test",
        password_hash="unused",
        role=Role.MEMBER,
        organization_id=setup_test_data["org"].id,
        is_active=True,
        is_verified=True,
    )
    sensor = setup_test_data["sensor"]
    recording = WavFile(
        id=uuid4(),
        sensor_id=sensor.id,
        gateway_id=sensor.gateway_id,
        sensor_mac=sensor.mac_address,
        s3_key="private.wav",
        file_size_bytes=1520,
        duration_seconds=2,
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
    )
    db.add_all([member, recording])
    db.commit()
    monkeypatch.setenv("RAW_ARCHIVE_READS_ENABLED", "true")

    def forbidden(*args, **kwargs):
        pytest.fail("Unauthorized request reached archive storage")

    monkeypatch.setattr(reader, "restore_object", forbidden)
    headers = {"Authorization": "Bearer " + create_access_token({"sub": str(member.id)})}
    assert client.get(f"/api/v1/wav/download/{recording.id}", headers=headers).status_code == 404
