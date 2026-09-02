"""Verified WAV feature extraction and retention safety tests."""

import hashlib
import io
import os
import uuid
import wave
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from sqlalchemy.orm import Session

from app.config import settings
from app.models.timeseries import SensorReading
from app.models.wav_file import WavFeature, WavFeatureVersion, WavFile
from app.services.retention_service import (
    _delete_expired_features,
    _delete_expired_wavs,
    retention_dry_run,
)
from app.services.wav_feature_service import (
    EXTRACTOR_VERSION,
    _next_candidate,
    calculate_sequence_continuity,
    calculate_signal_features,
    extract_and_verify_wav_features,
)


def _wav_bytes(samples: np.ndarray, sample_rate: int = 380) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(sample_rate)
        target.writeframes(samples.astype("<i2").tobytes())
    return output.getvalue()


def _wav_record(now: datetime, payload: bytes, *, days_old: int = 1) -> WavFile:
    started_at = now - timedelta(days=days_old)
    return WavFile(
        sensor_id=uuid.uuid4(),
        gateway_id=uuid.uuid4(),
        sensor_mac="AA:BB:CC:DD:EE:FF",
        s3_key=f"wav/test/{uuid.uuid4()}.wav",
        content_sha256=hashlib.sha256(payload).hexdigest(),
        sample_rate=380,
        duration_seconds=1.0,
        coverage_ratio=1.0,
        timing_status="complete",
        file_size_bytes=len(payload),
        started_at=started_at,
        ended_at=started_at + timedelta(seconds=1),
    )


def test_time_and_frequency_features_preserve_signal_information():
    sample_rate = 380
    time_axis = np.arange(sample_rate * 10) / sample_rate
    samples = (16_000 + 1_000 * np.sin(2 * np.pi * 5 * time_axis)).astype(np.float64)

    features, anomaly_index = calculate_signal_features(samples, sample_rate)

    assert features["mean"] == pytest.approx(16_000, abs=1)
    assert features["median"] == pytest.approx(16_000, abs=1)
    assert features["rms"] > features["mean"]
    assert features["standard_deviation"] == pytest.approx(707.1, rel=0.02)
    assert features["minimum"] == pytest.approx(15_000, abs=2)
    assert features["maximum"] == pytest.approx(17_000, abs=2)
    assert features["quantiles"]["p05"] < features["quantiles"]["p95"]
    assert features["dominant_frequency_hz"] == pytest.approx(5.0, abs=0.15)
    assert features["spectral_energy_total"] > 0
    assert 0 <= anomaly_index < samples.size


def test_sequence_gaps_and_resets_are_preserved(db: Session):
    now = datetime(2026, 9, 1, 12, tzinfo=UTC)
    wav = WavFile(
        sensor_id=uuid.uuid4(),
        gateway_id=uuid.uuid4(),
        sensor_mac="AA:BB:CC:DD:EE:FF",
        s3_key="wav/test/sequence.wav",
        sample_rate=380,
        duration_seconds=4,
        coverage_ratio=1,
        timing_status="complete",
        file_size_bytes=3_084,
        started_at=now,
        ended_at=now + timedelta(seconds=4),
    )
    for offset, sequence, dropped in ((0, 10, 5), (1, 11, 5), (2, 14, 8), (3, 2, 8)):
        db.add(
            SensorReading(
                timestamp=now + timedelta(seconds=offset),
                sensor_id=wav.sensor_id,
                kind="bio_signal",
                value=1,
                unit="mV",
                source_sequence=sequence,
                source_dropped_samples_total=dropped,
            )
        )
    db.commit()

    continuity = calculate_sequence_continuity(db, wav)

    assert continuity["sequence_observations"] == 4
    assert continuity["sequence_gap_count"] == 1
    assert continuity["sequence_missing_count"] == 2
    assert continuity["sequence_reset_count"] == 1
    assert continuity["source_dropped_samples_delta"] == 3


def test_extraction_verifies_checksum_before_marking_complete(
    db: Session,
    monkeypatch,
):
    now = datetime(2026, 9, 1, 12, tzinfo=UTC)
    payload = _wav_bytes(np.arange(380, dtype=np.int16))
    record = _wav_record(now, payload)
    db.add(record)
    db.commit()

    def download_object(_key, destination):
        destination.write(payload)
        destination.seek(0)

    monkeypatch.setattr(
        "app.services.wav_feature_service.wav_service.download_object", download_object
    )
    monkeypatch.setattr(settings, "wav_anomaly_archive_enabled", False)
    monkeypatch.setattr(settings, "wav_flac_archive_enabled", False)

    feature = extract_and_verify_wav_features(db, record)

    assert feature.extractor_version == EXTRACTOR_VERSION
    assert len(feature.feature_checksum) == 64
    assert feature.source_sha256 == hashlib.sha256(payload).hexdigest()
    assert feature.sample_count == 380
    assert feature.value_unit == "mV"
    assert feature.calibration_version == "nominal-adc-3v3-v1"
    assert db.query(WavFeatureVersion).count() == 1
    assert record.feature_status == "verified"
    assert record.feature_verified_at is not None


def test_unverified_raw_wav_is_never_deleted(
    db: Session,
    monkeypatch,
    mocker,
):
    now = datetime(2026, 9, 1, 12, tzinfo=UTC)
    payload = _wav_bytes(np.arange(380, dtype=np.int16))
    record = _wav_record(now, payload, days_old=91)
    db.add(record)
    db.commit()
    delete_object = mocker.patch("app.services.retention_service.wav_service.delete_wav")
    monkeypatch.setattr(settings, "retention_wav_days", 90)

    assert _delete_expired_wavs(db, now) == 0
    assert retention_dry_run(db, now)["wav_blocked_unverified"] == 1
    delete_object.assert_not_called()
    assert db.query(WavFile).one().raw_deleted_at is None
    assert db.query(WavFeature).count() == 0


def test_verified_features_are_retained_for_two_years(
    db: Session,
    monkeypatch,
):
    now = datetime(2026, 9, 1, 12, tzinfo=UTC)
    payload = _wav_bytes(np.arange(380, dtype=np.int16))
    records = [_wav_record(now, payload, days_old=900) for _ in range(2)]
    db.add_all(records)
    db.commit()

    def download_object(_key, destination):
        destination.write(payload)
        destination.seek(0)

    monkeypatch.setattr(
        "app.services.wav_feature_service.wav_service.download_object", download_object
    )
    monkeypatch.setattr(settings, "wav_anomaly_archive_enabled", False)
    monkeypatch.setattr(settings, "wav_flac_archive_enabled", False)
    monkeypatch.setattr(settings, "retention_wav_feature_days", 730)
    monkeypatch.setattr(settings, "retention_batch_size", 10)

    expired = extract_and_verify_wav_features(db, records[0])
    retained = extract_and_verify_wav_features(db, records[1])
    records[0].raw_deleted_at = now
    records[1].raw_deleted_at = now
    expired.verified_at = now - timedelta(days=731)
    retained.verified_at = now - timedelta(days=729)
    db.commit()

    assert _delete_expired_features(db, now) == 1
    assert db.query(WavFeature).one().id == retained.id


def test_recalculation_preserves_immutable_feature_versions(db: Session, monkeypatch):
    now = datetime(2026, 9, 1, 12, tzinfo=UTC)
    payload = _wav_bytes(np.arange(380, dtype=np.int16))
    record = _wav_record(now, payload)
    db.add(record)
    db.commit()

    def download_object(_key, destination):
        destination.write(payload)
        destination.seek(0)

    monkeypatch.setattr(
        "app.services.wav_feature_service.wav_service.download_object", download_object
    )
    monkeypatch.setattr(settings, "wav_anomaly_archive_enabled", False)
    monkeypatch.setattr(settings, "wav_flac_archive_enabled", False)

    extract_and_verify_wav_features(db, record)
    monkeypatch.setattr("app.services.wav_feature_service.EXTRACTOR_VERSION", "wav-v2.1.0")
    extract_and_verify_wav_features(db, record)

    versions = db.query(WavFeatureVersion).order_by(WavFeatureVersion.extractor_version).all()
    assert [version.extractor_version for version in versions] == ["wav-v2.0.0", "wav-v2.1.0"]
    assert versions[0].feature_payload["extractor_version"] == "wav-v2.0.0"


def test_calibration_change_requires_recalculation_before_retention(
    db: Session,
    monkeypatch,
    mocker,
):
    now = datetime(2026, 9, 1, 12, tzinfo=UTC)
    payload = _wav_bytes(np.arange(380, dtype=np.int16))
    record = _wav_record(now, payload, days_old=91)
    db.add(record)
    db.commit()

    def download_object(_key, destination):
        destination.write(payload)
        destination.seek(0)

    monkeypatch.setattr(
        "app.services.wav_feature_service.wav_service.download_object", download_object
    )
    monkeypatch.setattr(settings, "wav_anomaly_archive_enabled", False)
    monkeypatch.setattr(settings, "wav_flac_archive_enabled", False)
    monkeypatch.setattr(settings, "retention_wav_days", 90)
    extract_and_verify_wav_features(db, record)

    record.calibration_version = "calibrated-v2"
    db.commit()
    delete_object = mocker.patch("app.services.retention_service.wav_service.delete_wav")

    assert _next_candidate(db, now).id == record.id
    assert _delete_expired_wavs(db, now) == 0
    delete_object.assert_not_called()


@pytest.mark.integration
def test_minio_feature_and_flac_pipeline(monkeypatch):
    if os.getenv("IN_DOCKER_TEST") != "1":
        pytest.skip("requires the isolated Docker stack")

    from app.database import SessionLocal
    from app.services import wav_service

    now = datetime.now(UTC) - timedelta(minutes=1)
    payload = _wav_bytes(np.zeros(380, dtype=np.int16))
    record = _wav_record(now, payload)
    record.started_at = now
    record.ended_at = now + timedelta(seconds=1)
    record.s3_key = wav_service.upload_wav(
        io.BytesIO(payload),
        record.sensor_mac,
        record.started_at,
        len(payload),
        record.content_sha256,
    )
    monkeypatch.setattr(settings, "wav_anomaly_archive_enabled", True)
    monkeypatch.setattr(settings, "wav_flac_archive_enabled", True)

    with SessionLocal() as database:
        database.add(record)
        database.commit()
        feature = None
        try:
            feature = extract_and_verify_wav_features(database, record)
            assert feature.is_anomaly is False
            assert feature.data_quality_status == "technical_fault"
            assert feature.flac_verified_at is not None
            assert feature.anomaly_verified_at is None
            assert b"".join(wav_service.stream_wav_bytes(feature.flac_s3_key)).startswith(b"fLaC")
        finally:
            database.rollback()
            refreshed = database.query(WavFile).filter(WavFile.id == record.id).first()
            if refreshed and refreshed.feature:
                if refreshed.feature.flac_s3_key:
                    wav_service.delete_artifact(refreshed.feature.flac_s3_key)
                if refreshed.feature.anomaly_s3_key:
                    wav_service.delete_artifact(refreshed.feature.anomaly_s3_key)
            wav_service.delete_wav(record.s3_key)
            if refreshed:
                database.delete(refreshed)
                database.commit()
