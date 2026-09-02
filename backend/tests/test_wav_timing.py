"""WAV timing compatibility tests."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.config import settings
from app.models.wav_file import WavFeature, WavFile
from app.schemas.gateway import HeartbeatRequest
from app.schemas.ingest import ReadingPayload
from app.services.retention_service import _delete_expired_wavs
from app.services.wav_feature_service import EXTRACTOR_VERSION, PARAMETER_HASH
from app.services.wav_service import reconcile_wav_timing


def test_sparse_legacy_window_is_accepted_as_partial():
    now = datetime(2026, 9, 1, 12, tzinfo=UTC)
    start = now - timedelta(minutes=20)
    end = start + timedelta(minutes=10)

    reconciled_end, coverage, status = reconcile_wav_timing(
        start,
        end,
        285.0,
        now=now,
    )

    assert reconciled_end == end
    assert coverage == pytest.approx(0.475)
    assert status == "partial"


def test_invalid_legacy_end_is_inferred_from_frames():
    now = datetime(2026, 9, 1, 12, tzinfo=UTC)
    start = now - timedelta(minutes=20)

    reconciled_end, coverage, status = reconcile_wav_timing(
        start,
        start - timedelta(seconds=1),
        60.0,
        now=now,
    )

    assert reconciled_end == start + timedelta(seconds=60)
    assert coverage == 1.0
    assert status == "inferred"


def test_future_audio_window_remains_rejected():
    now = datetime(2026, 9, 1, 12, tzinfo=UTC)
    start = now + timedelta(minutes=20)

    with pytest.raises(ValueError, match="future"):
        reconcile_wav_timing(start, start, 60.0, now=now)


def test_gateway_and_sensor_continuity_telemetry_validates():
    heartbeat = HeartbeatRequest(
        hardware_id="gateway-1",
        wav_pending_files=3,
        wav_pending_bytes=4096,
        wav_oldest_pending_age_hours=2.5,
        wav_last_upload_at=datetime(2026, 9, 1, 12, tzinfo=UTC),
        wav_last_error_code="http_422",
    )
    reading = ReadingPayload(
        sensor_mac="AA:BB:CC:DD:EE:FF",
        sensor_kind="bio_signal",
        value=123.0,
        unit="mV",
        source_sequence=42,
        source_uptime_ms=123_456,
        source_dropped_samples_total=7,
    )

    assert heartbeat.wav_pending_files == 3
    assert reading.source_sequence == 42


def test_legacy_gateway_heartbeat_remains_valid():
    heartbeat = HeartbeatRequest(
        hardware_id="gateway-1",
        local_ip="192.0.2.10",
        cpu_temp_c=45.0,
        ram_usage_pct=30.0,
        wifi_rssi_dbm=-60,
        queue_depth=5,
    )

    assert heartbeat.wav_pending_files is None


def test_wav_retention_deletes_object_before_metadata(
    db: Session,
    monkeypatch,
    mocker,
):
    now = datetime(2026, 9, 1, 12, tzinfo=UTC)
    record = WavFile(
        sensor_id=uuid.uuid4(),
        gateway_id=uuid.uuid4(),
        sensor_mac="AA:BB:CC:DD:EE:FF",
        s3_key="wav/test/old.wav",
        sample_rate=380,
        duration_seconds=60,
        file_size_bytes=45_644,
        started_at=now - timedelta(days=91),
        ended_at=now - timedelta(days=91) + timedelta(seconds=60),
        feature_status="verified",
        feature_verified_at=now - timedelta(days=90),
    )
    db.add(record)
    db.flush()
    db.add(
        WavFeature(
            wav_file_id=record.id,
            sensor_id=record.sensor_id,
            gateway_id=record.gateway_id,
            sensor_mac=record.sensor_mac,
            started_at=record.started_at,
            ended_at=record.ended_at,
            extractor_version=EXTRACTOR_VERSION,
            parameter_hash=PARAMETER_HASH,
            feature_checksum="a" * 64,
            source_sha256="b" * 64,
            sample_rate=380,
            sample_count=22_800,
            duration_seconds=60,
            value_unit="pcm_int16",
            mean=1,
            median=1,
            rms=1,
            standard_deviation=0,
            minimum=1,
            maximum=1,
            quantiles={},
            outlier_count=0,
            outlier_ratio=0,
            clipping_count=0,
            clipping_ratio=0,
            coverage_ratio=1,
            timing_status="complete",
            missing_duration_seconds=0,
            flatline_count=0,
            flatline_seconds=0,
            sequence_observations=0,
            sequence_gap_count=0,
            sequence_missing_count=0,
            sequence_reset_count=0,
            spectral_energy_total=0,
            dominant_frequency_hz=0,
            spectral_bands={},
            is_anomaly=False,
            anomaly_score=0,
            anomaly_reasons=[],
            verified_at=now - timedelta(days=90),
        )
    )
    db.commit()
    delete_object = mocker.patch("app.services.retention_service.wav_service.delete_wav")
    monkeypatch.setattr(settings, "retention_wav_days", 90)
    monkeypatch.setattr(settings, "retention_batch_size", 10)

    deleted = _delete_expired_wavs(db, now)

    assert deleted == 1
    delete_object.assert_called_once_with("wav/test/old.wav")
    assert db.query(WavFile).one().raw_deleted_at.replace(tzinfo=UTC) == now
    assert db.query(WavFeature).count() == 1
