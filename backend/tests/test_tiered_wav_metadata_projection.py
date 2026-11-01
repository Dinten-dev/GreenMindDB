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
    db.execute(
        text(
            "CREATE TABLE visual_wav(wav_id TEXT PRIMARY KEY,source_sha256 TEXT,seconds INTEGER,status TEXT,updated_at TEXT,error TEXT)"
        )
    )
    db.execute(
        text(
            "INSERT INTO visual_wav VALUES(:id,:sha,60,'verified','2026-09-07T00:00:00+00:00',:error)"
        ),
        {"id": record.id.hex, "sha": record.content_sha256, "error": PRIVATE},
    )
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
                "CREATE TABLE direct_visual_segment(segment_id TEXT PRIMARY KEY,source_revision INTEGER,seconds INTEGER,source TEXT,updated_at TEXT,error TEXT,retry_at REAL)"
            )
        )
        db.execute(text("CREATE TABLE direct_visual_point(segment_id TEXT)"))
        db.execute(
            text(
                "INSERT INTO direct_visual_segment VALUES(:id,1,600,'wav','2026-10-01T00:00:02+00:00',NULL,0)"
            ),
            {"id": segment},
        )
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


def test_real_gateway_copy_producer_marks_current_assignment_only_observed(
    gateway_adapter, tmp_path
):
    from app.raw_archive.wav_context import GATEWAY_COPY_SCOPE_V2, UNKNOWN, copy_flags

    adapter, reference, _, _, _ = gateway_adapter
    strict, _, _ = adapter.metadata(reference)
    adapter.copy_published_history = True
    copied, headers, flags = adapter.metadata(reference)
    assert copied["body"]["copy_scope"] == GATEWAY_COPY_SCOPE_V2
    assert flags == copy_flags()
    assert copied["body"]["metadata"]["capture_assignment"] == UNKNOWN
    assert copied["body"]["metadata"]["observed_assignment"] == {
        **strict["body"]["metadata"]["assignment"],
        "semantics": "observed_at_archive",
    }
    for key in ("wav", "feature", "feature_versions"):
        assert encode(copied["body"]["metadata"][key]) == encode(strict["body"]["metadata"][key])
    assert set(copied["body"]["metadata"]["visual_wav"]) == {
        "wav_id",
        "source_sha256",
        "seconds",
        "status",
        "updated_at",
    }
    assert PRIVATE.encode() not in parquet_roundtrip(copied, tmp_path / "observed-gateway.parquet")
    assert next(iter(headers.values()))["frames"] == 380
    with pytest.raises(ArchiveBlocked, match="COPY-only"):
        adapter._require_deletion_scope()


def test_real_current_published_direct_copy_producer_is_observed_not_historical(
    direct_adapter, tmp_path
):
    from app.raw_archive.direct_copy_catalog import PublishedDirectCopyCatalog
    from app.raw_archive.wav_context import DIRECT_COPY_SCOPE_V2, UNKNOWN, copy_flags

    adapter, reference, sessions, _ = direct_adapter
    strict, _, _ = adapter.metadata(reference)
    adapter.catalog = PublishedDirectCopyCatalog(sessions, "greenmind-direct-staging-test")
    assert adapter.catalog.historical_metadata(reference) is None
    adapter.copy_published_history = True
    copied, _, flags = adapter.metadata(reference)
    assert copied["body"]["copy_scope"] == DIRECT_COPY_SCOPE_V2 and flags == copy_flags()
    assert copied["body"]["metadata"]["capture_assignment"] == UNKNOWN
    assert copied["body"]["metadata"]["observed_assignment"] == {
        **strict["body"]["metadata"]["assignment"],
        "semantics": "observed_at_archive",
    }
    for key in ("segment", "revision", "session"):
        assert encode(copied["body"]["metadata"][key]) == encode(strict["body"]["metadata"][key])
    assert PRIVATE.encode() not in parquet_roundtrip(copied, tmp_path / "observed-direct.parquet")
    with pytest.raises(ArchiveBlocked, match="COPY-only"):
        adapter._require_deletion_scope()


def test_gateway_v2_does_not_invent_absent_visual_parent(gateway_adapter):
    adapter, reference, _, _, db = gateway_adapter
    db.execute(text("DELETE FROM visual_wav"))
    db.commit()
    adapter.copy_published_history = True
    copied, _, _ = adapter.metadata(reference)
    assert copied["body"]["metadata"]["visual_wav"] is None


def test_gateway_v2_visual_source_must_match_current_wav(gateway_adapter):
    adapter, reference, _, _, db = gateway_adapter
    db.execute(text("UPDATE visual_wav SET source_sha256=:sha"), {"sha": "0" * 64})
    db.commit()
    adapter.copy_published_history = True
    with pytest.raises(ArchiveBlocked, match="visual WAV parent source identity"):
        adapter.metadata(reference)


def test_gateway_v2_source_change_during_real_copy_is_blocked(gateway_adapter, tmp_path):
    adapter, reference, _, _, db = gateway_adapter

    class Objects:
        def snapshot(self, record):
            return {"ETag": "same-original"}

        def download(self, record, snapshot, path):
            path.write_bytes(_wav_bytes(np.arange(380, dtype=np.int16)))
            db.execute(text("UPDATE visual_wav SET status='pending'"))
            db.commit()

    adapter.copy_published_history = True
    adapter.objects = Objects()
    with pytest.raises(ArchiveBlocked, match="scientific source changed"):
        adapter.export(reference, tmp_path)


@pytest.mark.parametrize("kind", ["gateway", "direct"])
def test_observed_v2_receipt_never_authorizes_delete(kind):
    from app.raw_archive.wav_context import (
        DIRECT_COPY_SCOPE_V2,
        GATEWAY_COPY_SCOPE_V2,
        receipt_context,
        require_deletable_receipt,
    )

    context = receipt_context(GATEWAY_COPY_SCOPE_V2 if kind == "gateway" else DIRECT_COPY_SCOPE_V2)
    assert context["deletion_authorized"] is False
    with pytest.raises(ArchiveBlocked, match="COPY-only"):
        require_deletable_receipt({"source_identity": context})


def test_direct_v2_preserves_real_projection_state_and_byte_exact_parquet(direct_adapter, tmp_path):
    from app.raw_archive.direct_copy_catalog import PublishedDirectCopyCatalog

    adapter, reference, sessions, engine = direct_adapter
    with engine.begin() as db:
        db.execute(
            text("UPDATE direct_visual_segment SET retry_at=1790000000.25 WHERE segment_id=:id"),
            {"id": reference[0]},
        )
    adapter.catalog = PublishedDirectCopyCatalog(sessions, "greenmind-direct-staging-test")
    adapter.copy_published_history = True
    envelope, _, _ = adapter.metadata(reference)
    parent = envelope["body"]["metadata"]["direct_visual_segment"]
    assert parent["segment_id"] == reference[0] and parent["source_revision"] == 1
    assert parent["error"] is None and parent["retry_at"] == 1790000000.25
    part = parquet.write(tmp_path / "parent.parquet", [envelope])
    restored = list(parquet.read(tmp_path / "parent.parquet", part))[0]
    assert encode(restored) == encode(envelope)
    assert PRIVATE.encode() not in encode(restored)


def test_direct_v2_absent_projection_remains_explicitly_absent(direct_adapter):
    from app.raw_archive.direct_copy_catalog import PublishedDirectCopyCatalog

    adapter, reference, sessions, engine = direct_adapter
    with engine.begin() as db:
        db.execute(text("DELETE FROM direct_visual_segment"))
    adapter.catalog = PublishedDirectCopyCatalog(sessions, "greenmind-direct-staging-test")
    adapter.copy_published_history = True
    with pytest.raises(ArchiveBlocked, match="projection missing or stale"):
        adapter.metadata(reference)


def test_gateway_versioned_signal_history_is_archived_and_unknown_is_preserved(
    gateway_adapter, tmp_path
):
    from app.models.wav_file import WavFeatureVersion
    from app.services.signal_feature_contract import canonical_sha256
    from app.services.wav_feature_service import gateway_contract_payload

    adapter, reference, record_id, _, db = gateway_adapter
    envelope, _, _ = adapter.metadata(reference)
    source_sha = envelope["body"]["metadata"]["wav"]["content_sha256"]
    descriptor = dict(
        status="declared",
        scale=1.0,
        offset=0.0,
        unit="mV",
        source="fixture",
        channel="CH1",
        encoding="signed",
        validity="fixture",
    )
    flags = dict.fromkeys(("valid", "lead_off", "rail_high", "rail_low", "jump", "recovery"))
    signal = gateway_contract_payload(
        np.arange(1, 381, dtype=np.int32),
        380,
        descriptor=descriptor,
        continuity={"sequence_gap_count": None, "source_dropped_samples_delta": None},
        source_quality=flags,
        coverage_ratio=1.0,
        observations={
            "coverage": True,
            "continuity": False,
            "source_flags": False,
            "dropped_samples": False,
        },
    )
    payload = {
        "schema": "gateway-signal-v1",
        "wav_file_id": str(record_id),
        "source_sha256": source_sha,
        "signal": signal,
    }
    db.add(
        WavFeatureVersion(
            id=uuid.uuid4(),
            wav_file_id=record_id,
            extractor_version="gateway-signal-v1-" + "a" * 16,
            calibration_version="fixture",
            parameter_hash="b" * 64,
            source_sha256=source_sha,
            feature_checksum=canonical_sha256(payload),
            feature_payload=payload,
            verified_at=datetime.now(UTC),
        )
    )
    db.commit()
    envelope, _, _ = adapter.metadata(reference)
    assert envelope["body"]["feature_history_format"] == "gateway-signal-history-v2"
    result = next(
        version
        for version in envelope["body"]["metadata"]["feature_versions"]
        if version["feature_payload"].get("schema") == "gateway-signal-v1"
    )
    assert result["feature_payload"]["signal"]["quality"]["data_quality_status"] == "unknown"
    assert b"gateway-signal-history-v2" in parquet_roundtrip(
        envelope, tmp_path / "gateway-v2.parquet"
    )
