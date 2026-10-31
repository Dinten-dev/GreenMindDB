"""Real SELECT and Parquet regressions for narrow production copy readers."""

import uuid
from contextlib import nullcontext
from datetime import UTC, datetime

import numpy as np
import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.raw_archive.catalog import DirectCatalog, GatewayCatalog, canonical
from app.raw_archive.policy import ArchiveBlocked
from app.tiered_archive import parquet
from app.tiered_archive.codec import encode
from app.tiered_archive.wav_source import WavSource
from tests.test_wav_features import _wav_bytes, _wav_record

PRIVATE = "PRIVATE-AUTH-AND-ERROR-MUST-NOT-BE-ARCHIVED"
FORBIDDEN = {
    "wav_file": {"feature_attempts", "feature_error", "feature_started_at"},
    "wav_feature": {
        prefix + suffix
        for prefix in ("anomaly_", "flac_")
        for suffix in ("s3_key", "sha256", "file_size_bytes", "verified_at", "expires_at")
    },
    "direct_device": {"key_hash", "active", "spool_bytes"},
    "direct_segment": {"error", "retry_at"},
}


def narrow_sql(statement):
    if statement.lstrip().upper().startswith("SELECT"):
        for table, columns in FORBIDDEN.items():
            for column in columns:
                assert table + "." + column not in statement


def parquet_roundtrip(envelope, path):
    receipt = parquet.write(path, [envelope])
    return encode(list(parquet.read(path, receipt)))


@pytest.fixture
def gateway_adapter(db, setup_test_data, monkeypatch):
    from types import SimpleNamespace

    from app.raw_archive import metadata_projection
    from app.services.wav_feature_service import extract_and_verify_wav_features

    # SQLite drops timestamptz information; emulate the real PostgreSQL driver
    # result type without changing any selected column or archive/readiness gate.
    monkeypatch.setattr(
        metadata_projection,
        "SimpleNamespace",
        lambda **values: SimpleNamespace(
            **{
                key: value.replace(tzinfo=UTC)
                if isinstance(value, datetime) and value.tzinfo is None
                else value
                for key, value in values.items()
            }
        ),
    )
    payload = _wav_bytes(np.arange(380, dtype=np.int16))
    record = _wav_record(datetime.now(UTC), payload)
    record.sensor_id = setup_test_data["sensor"].id
    record.gateway_id = setup_test_data["gateway"].id
    db.add(record)
    db.commit()

    def download(_key, destination):
        destination.write(payload)
        destination.seek(0)

    monkeypatch.setattr("app.services.wav_feature_service.wav_service.download_object", download)
    monkeypatch.setattr(settings, "wav_anomaly_archive_enabled", False)
    monkeypatch.setattr(settings, "wav_flac_archive_enabled", False)
    feature = extract_and_verify_wav_features(db, record)
    record.feature_error = PRIVATE
    feature.anomaly_s3_key = PRIVATE
    feature.flac_s3_key = PRIVATE + "-flac"
    db.execute(text("CREATE TABLE visual_wav(wav_id TEXT PRIMARY KEY,status TEXT)"))
    db.execute(text("INSERT INTO visual_wav VALUES(:id,'verified')"), {"id": record.id.hex})
    db.commit()
    identity = str(record.id)
    feature_id = feature.id
    engine = db.get_bind()

    def restricted(connection, cursor, statement, parameters, context, many):
        narrow_sql(statement)
        if isinstance(parameters, tuple):
            parameters = tuple(v.hex if isinstance(v, uuid.UUID) else v for v in parameters)
        return statement, parameters

    event.listen(engine, "before_cursor_execute", restricted, retval=True)
    def sessions():
        return nullcontext(db)
    adapter = WavSource(
        sessions, GatewayCatalog(sessions), None, "gateway", "staging", None, lambda: None
    )
    yield adapter, [identity], uuid.UUID(identity), feature_id, db
    event.remove(engine, "before_cursor_execute", restricted)
    db.rollback()
    db.execute(text("DROP TABLE visual_wav"))
    db.commit()


def test_gateway_real_selects_and_parquet_exclude_private_fields(gateway_adapter, tmp_path):
    adapter, reference, _, _, _ = gateway_adapter
    envelope, headers, flags = adapter.metadata(reference)
    assert all(flags.values())
    assert len(envelope["body"]["metadata"]["feature_versions"]) == 1
    assert next(iter(headers.values()))["frames"] == 380
    encoded = parquet_roundtrip(envelope, tmp_path / "gateway.parquet")
    assert PRIVATE.encode() not in encoded
    for field in ("feature_error", "feature_attempts", "anomaly_s3_key", "flac_s3_key"):
        assert field.encode() not in encoded
    assert b"calibration_version" in encoded and b"feature_versions" in encoded


@pytest.mark.parametrize("change", ["unverified", "checksum", "source_checksum"])
def test_gateway_current_feature_gates_still_block(gateway_adapter, change):
    from app.models.wav_file import WavFeature, WavFile

    adapter, reference, record_id, feature_id, db = gateway_adapter
    if change == "unverified":
        db.query(WavFile).filter_by(id=record_id).update(
            {"feature_status": "pending"}, synchronize_session=False
        )
    else:
        field = "feature_checksum" if change == "checksum" else "source_sha256"
        db.query(WavFeature).filter_by(id=feature_id).update(
            {field: "0" * 64}, synchronize_session=False
        )
    db.commit()
    with pytest.raises(ArchiveBlocked, match="features are not verified"):
        adapter.metadata(reference)


def test_nested_feature_version_auth_field_is_rejected(gateway_adapter):
    from app.models.wav_file import WavFeatureVersion

    adapter, reference, record_id, _, db = gateway_adapter
    version = db.query(WavFeatureVersion).filter_by(wav_file_id=record_id).one()
    version.feature_payload = dict(version.feature_payload, key_hash=PRIVATE)
    db.commit()
    with pytest.raises(ArchiveBlocked, match="Unapproved fields"):
        adapter.metadata(reference)


@pytest.fixture
def direct_adapter():
    from app.direct.models import Base, Device, Enrollment, Revision, Segment
    from app.direct.models import Session as CaptureSession

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    device, session, segment = [str(uuid.uuid4()) for _ in range(3)]
    started = int(datetime(2026, 10, 1, tzinfo=UTC).timestamp() * 1000000)
    config = dict(
        protocol_version=1,
        device_id=device,
        session_id=session,
        session_start_us=started,
        sample_rate=380,
        channels=1,
        sample_bits=16,
        channel_labels=["leaf0"],
        calibration_version="unsigned-mv-linear-int16-v1",
        firmware_version="test-v1",
    )
    manifest = dict(
        protocol_version=1,
        device_id=device,
        session_id=session,
        bucket_start_us=started,
        revision=1,
        config=config,
        source_mode="DIRECT",
        analytics_scope="direct",
        first_frame=0,
        end_frame=380,
        initial_partial=False,
        status="complete",
        missing_frame_ranges=[],
        runs=[
            dict(
                first_frame=0,
                frame_count=380,
                started_at_us=started,
                key="original.wav",
                sha256="a" * 64,
                features=[
                    dict(
                        channel=0,
                        mean=1,
                        sample_count=380,
                        extractor_version="direct-v1",
                        parameter_hash="b" * 64,
                    )
                ],
            )
        ],
    )
    with sessions() as db:
        db.add(
            Device(
                id=device,
                organization_id=str(uuid.uuid4()),
                zone_id=str(uuid.uuid4()),
                mode="DIRECT",
                key_hash=PRIVATE,
            )
        )
        db.add(CaptureSession(device_id=device, id=session, config=config, mode="DIRECT"))
        db.add(Enrollment(hardware_id="14:c1:9f:d9:42:a4", device_id=device))
        db.add(
            Segment(
                id=segment,
                device_id=device,
                session_id=session,
                bucket=started // 600000000,
                first_frame=0,
                end_frame=380,
                received_frames=380,
                revision=1,
                published_revision=1,
                sealed=True,
                updated_at=started / 1000000 + 1,
                error=PRIVATE,
            )
        )
        db.add(
            Revision(
                segment_id=segment,
                revision=1,
                manifest=manifest,
                manifest_sha256=canonical(manifest),
                verified_at=started / 1000000 + 2,
            )
        )
        db.execute(
            text(
                "CREATE TABLE direct_visual_segment(segment_id TEXT PRIMARY KEY,source_revision INTEGER,error TEXT)"
            )
        )
        db.execute(text("CREATE TABLE direct_visual_point(segment_id TEXT)"))
        db.execute(text("INSERT INTO direct_visual_segment VALUES(:id,1,NULL)"), {"id": segment})
        db.execute(text("INSERT INTO direct_visual_point VALUES(:id)"), {"id": segment})
        db.commit()

    def restricted(connection, cursor, statement, parameters, context, many):
        narrow_sql(statement)

    event.listen(engine, "before_cursor_execute", restricted)
    adapter = WavSource(
        sessions,
        DirectCatalog(sessions, "greenmind-direct-staging-test"),
        None,
        "direct",
        "staging",
        None,
        lambda: None,
    )
    yield adapter, [segment, 1], sessions, engine
    event.remove(engine, "before_cursor_execute", restricted)
    engine.dispose()


def test_direct_real_selects_and_parquet_exclude_auth(direct_adapter, tmp_path):
    adapter, reference, _, _ = direct_adapter
    envelope, _, flags = adapter.metadata(reference)
    assert all(flags.values())
    encoded = parquet_roundtrip(envelope, tmp_path / "direct.parquet")
    assert PRIVATE.encode() not in encoded
    assert b'"key_hash"' not in encoded and b'"retry_at"' not in encoded
    assert b'"session_start_us"' in encoded and b'"missing_frame_ranges"' in encoded


@pytest.mark.parametrize("change", ["unsealed", "new_revision", "projection_error"])
def test_direct_readiness_revision_gates_remain(direct_adapter, change):
    from app.direct.models import Segment

    adapter, reference, sessions, _ = direct_adapter
    with sessions() as db:
        if change == "projection_error":
            db.execute(text("UPDATE direct_visual_segment SET error=:error"), {"error": PRIVATE})
        else:
            # Setup writes occur before, and are not part of the monitored archive read.
            values = {"sealed": False} if change == "unsealed" else {"revision": 2}
            db.query(Segment).filter_by(id=reference[0]).update(values, synchronize_session=False)
        db.commit()
    with pytest.raises(ArchiveBlocked):
        adapter.metadata(reference)


def test_missing_projection_error_permission_is_not_bypassed(direct_adapter):
    adapter, reference, _, engine = direct_adapter

    def denied(connection, cursor, statement, parameters, context, many):
        if "SELECT source_revision, error" in statement:
            raise PermissionError("missing necessary projection readiness metadata")

    event.listen(engine, "before_cursor_execute", denied)
    try:
        with pytest.raises(PermissionError, match="readiness"):
            adapter.metadata(reference)
    finally:
        event.remove(engine, "before_cursor_execute", denied)
