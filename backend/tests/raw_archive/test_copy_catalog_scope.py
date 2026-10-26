"""A narrow copy reader never requests unrelated ORM fields or feature relations."""

import uuid
from contextlib import nullcontext
from datetime import UTC, datetime

import pytest
from sqlalchemy import event

from app.models.wav_file import WavFile
from app.raw_archive.catalog import GatewayCatalog
from app.raw_archive.policy import ArchiveBlocked


def test_copy_uses_only_named_metadata_columns(db, setup_test_data):
    sensor = setup_test_data["sensor"]
    now = datetime.now(UTC)
    original = WavFile(
        id=uuid.uuid4(),
        sensor_id=sensor.id,
        gateway_id=sensor.gateway_id,
        sensor_mac=sensor.mac_address,
        s3_key="private-original.wav",
        content_sha256="a" * 64,
        file_size_bytes=456044,
        duration_seconds=600,
        started_at=now,
        ended_at=now,
        created_at=now,
    )
    db.add(original)
    db.commit()
    identity = original.id
    calls = []

    def limited_read(connection, cursor, statement, parameters, context, many):
        if statement.lstrip().upper().startswith("SELECT"):
            calls.append(statement)
            assert "wav_feature" not in statement
            assert "wav_file.feature_error" not in statement
            assert "wav_file.pcm_encoding_version" not in statement

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", limited_read)
    try:
        copied = GatewayCatalog(lambda: nullcontext(db), copy_only=True).inspect(identity)
    finally:
        event.remove(engine, "before_cursor_execute", limited_read)
    assert len(calls) == 1 and copied.identity == str(identity)
    assert copied.sha256 == "a" * 64 and copied.key == "private-original.wav"
    assert copied.feature_digest == ""
    assert db.get(WavFile, identity).raw_deleted_at is None


def test_copy_rejects_already_evicted_original(db, setup_test_data):
    sensor = setup_test_data["sensor"]
    now = datetime.now(UTC)
    original = WavFile(
        id=uuid.uuid4(),
        sensor_id=sensor.id,
        gateway_id=sensor.gateway_id,
        sensor_mac=sensor.mac_address,
        s3_key="private-original.wav",
        file_size_bytes=456044,
        duration_seconds=600,
        started_at=now,
        ended_at=now,
        raw_deleted_at=now,
    )
    db.add(original)
    db.commit()
    with pytest.raises(ArchiveBlocked, match="previously deleted"):
        GatewayCatalog(lambda: nullcontext(db), copy_only=True).inspect(original.id)


def test_direct_copy_never_selects_ungranted_operational_columns():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.direct.models import Base, Enrollment, Revision, Segment
    from app.raw_archive.catalog import DirectCatalog, canonical

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    manifest = dict(
        device_id="device",
        session_id="session",
        revision=1,
        config=dict(channels=1, sample_bits=16, sample_rate=380),
        runs=[
            dict(
                key="original.wav",
                sha256="a" * 64,
                frame_count=760,
                started_at_us=1000000,
                features=[dict(mean=1)],
            )
        ],
    )
    with sessions() as db:
        db.add(
            Segment(
                id="segment",
                device_id="device",
                session_id="session",
                bucket=0,
                first_frame=0,
                end_frame=760,
                revision=1,
                published_revision=1,
                sealed=True,
                updated_at=601,
            )
        )
        db.add(
            Revision(
                segment_id="segment",
                revision=1,
                manifest=manifest,
                manifest_sha256=canonical(manifest),
                verified_at=602,
            )
        )
        db.add(Enrollment(hardware_id="14:c1:9f:d9:42:a4", device_id="device"))
        db.commit()

    def restricted(connection, cursor, statement, parameters, context, many):
        if statement.lstrip().upper().startswith("SELECT"):
            assert "direct_segment.retry_at" not in statement
            assert "direct_segment.error" not in statement

    event.listen(engine, "before_cursor_execute", restricted)
    try:
        records = DirectCatalog(sessions, "greenmind-direct-test", copy_only=True).inspect(
            "segment", 1
        )
        assert len(records) == 1 and records[0].key == "original.wav"
        assert records[0].sha256 == "a" * 64 and records[0].size == 1564
    finally:
        event.remove(engine, "before_cursor_execute", restricted)
        engine.dispose()
