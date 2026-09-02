"""Verified feature extraction for full-resolution WAV recordings."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import shutil
import subprocess
import tempfile
import wave
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.config import settings
from app.models.timeseries import SensorReading
from app.models.wav_file import WavFeature, WavFeatureVersion, WavFile
from app.services import wav_service

logger = logging.getLogger(__name__)

EXTRACTOR_VERSION = "wav-v2.0.0"
_PARAMETERS = {
    "outlier_sigma": 6.0,
    "flatline_minimum_seconds": 1.0,
    "technical_coverage_minimum": 0.95,
    "technical_clipping_ratio": 0.001,
    "biological_outlier_ratio": 0.001,
}
PARAMETER_HASH = hashlib.sha256(
    json.dumps(_PARAMETERS, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()
_SEQUENCE_MODULUS = 2**32
_SPECTRAL_BANDS = (
    ("ultra_low_0_003_0_04_hz", 0.003, 0.04),
    ("very_low_0_04_0_15_hz", 0.04, 0.15),
    ("low_0_15_0_5_hz", 0.15, 0.5),
    ("mid_0_5_1_hz", 0.5, 1.0),
    ("high_1_10_hz", 1.0, 10.0),
    ("very_high_10_50_hz", 10.0, 50.0),
)


def _rounded(value: float) -> float:
    if not math.isfinite(value):
        raise ValueError("Feature calculation produced a non-finite value")
    return round(float(value), 12)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_pcm_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as source:
        if (
            source.getnchannels() != 1
            or source.getsampwidth() != 2
            or source.getcomptype() != "NONE"
        ):
            raise ValueError("WAV must be mono, 16-bit, uncompressed PCM")
        sample_rate = source.getframerate()
        sample_count = source.getnframes()
        payload = source.readframes(sample_count)
    if len(payload) != sample_count * 2:
        raise ValueError("WAV payload is truncated")
    samples = np.frombuffer(payload, dtype="<i2").astype(np.float64)
    if not sample_count or not np.isfinite(samples).all():
        raise ValueError("WAV contains no valid samples")
    return samples, sample_rate


def _flatline_features(samples: np.ndarray, sample_rate: int) -> tuple[int, float, int | None]:
    boundaries = np.flatnonzero(np.diff(samples) != 0) + 1
    starts = np.concatenate((np.array([0]), boundaries))
    ends = np.concatenate((boundaries, np.array([samples.size])))
    lengths = ends - starts
    minimum_length = max(1, sample_rate)
    qualifying = np.flatnonzero(lengths >= minimum_length)
    if not qualifying.size:
        return 0, 0.0, None
    longest = int(qualifying[np.argmax(lengths[qualifying])])
    center = int(starts[longest] + lengths[longest] // 2)
    seconds = float(np.sum(lengths[qualifying]) / sample_rate)
    return int(qualifying.size), _rounded(seconds), center


def _spectral_features(samples: np.ndarray, sample_rate: int) -> tuple[float, float, dict]:
    maximum_fft_size = min(samples.size, 65_536)
    fft_size = 1 << (int(maximum_fft_size).bit_length() - 1)
    if fft_size < 16:
        return 0.0, 0.0, {name: 0.0 for name, _, _ in _SPECTRAL_BANDS}

    step = max(1, fft_size // 2)
    starts = list(range(0, samples.size - fft_size + 1, step)) or [0]
    window = np.hanning(fft_size)
    normalization = float(np.sum(window**2) * sample_rate)
    power = np.zeros(fft_size // 2 + 1, dtype=np.float64)
    for start in starts:
        segment = samples[start : start + fft_size]
        segment = segment - np.mean(segment)
        spectrum = np.fft.rfft(segment * window)
        power += (np.abs(spectrum) ** 2) / normalization
    power /= len(starts)
    frequencies = np.fft.rfftfreq(fft_size, d=1.0 / sample_rate)

    non_dc = frequencies >= max(0.003, sample_rate / fft_size)
    if np.any(non_dc):
        indices = np.flatnonzero(non_dc)
        dominant_index = int(indices[np.argmax(power[non_dc])])
        dominant_frequency = _rounded(frequencies[dominant_index])
    else:
        dominant_frequency = 0.0

    total_energy = _rounded(np.trapezoid(power[non_dc], frequencies[non_dc]))
    bands: dict[str, float] = {}
    nyquist = sample_rate / 2.0
    for name, lower, upper in _SPECTRAL_BANDS:
        upper = min(upper, nyquist)
        mask = (frequencies >= lower) & (frequencies < upper)
        bands[name] = (
            _rounded(np.trapezoid(power[mask], frequencies[mask])) if mask.sum() > 1 else 0.0
        )
    if nyquist > 50.0:
        mask = frequencies >= 50.0
        bands["noise_50_nyquist_hz"] = (
            _rounded(np.trapezoid(power[mask], frequencies[mask])) if mask.sum() > 1 else 0.0
        )
    return total_energy, dominant_frequency, bands


def calculate_signal_features(
    samples: np.ndarray,
    sample_rate: int,
    *,
    clipping_bounds: tuple[float, float] = (0.0, 32_767.0),
) -> tuple[dict, int]:
    """Calculate versioned time-domain and spectral features."""
    if sample_rate <= 0 or not samples.size:
        raise ValueError("Samples and sample rate are required")

    median = float(np.median(samples))
    absolute_deviation = np.abs(samples - median)
    mad = float(np.median(absolute_deviation))
    robust_sigma = 1.4826 * mad
    if robust_sigma > 0:
        outlier_mask = absolute_deviation > 6.0 * robust_sigma
    else:
        standard_deviation = float(np.std(samples))
        outlier_mask = (
            absolute_deviation > 6.0 * standard_deviation
            if standard_deviation
            else np.zeros(samples.size, dtype=bool)
        )

    clipping_mask = (samples <= clipping_bounds[0]) | (samples >= clipping_bounds[1])
    flatline_count, flatline_seconds, flatline_center = _flatline_features(samples, sample_rate)
    spectral_energy, dominant_frequency, spectral_bands = _spectral_features(samples, sample_rate)
    quantile_values = np.quantile(samples, [0.01, 0.05, 0.25, 0.75, 0.95, 0.99])
    quantiles = {
        key: _rounded(value)
        for key, value in zip(
            ("p01", "p05", "p25", "p75", "p95", "p99"),
            quantile_values,
            strict=True,
        )
    }

    if np.any(outlier_mask):
        anomaly_index = int(np.argmax(absolute_deviation * outlier_mask))
    elif np.any(clipping_mask):
        anomaly_index = int(np.flatnonzero(clipping_mask)[0])
    elif flatline_center is not None:
        anomaly_index = flatline_center
    else:
        anomaly_index = samples.size // 2

    features = {
        "mean": _rounded(np.mean(samples)),
        "median": _rounded(median),
        "rms": _rounded(np.sqrt(np.mean(samples**2))),
        "standard_deviation": _rounded(np.std(samples)),
        "minimum": _rounded(np.min(samples)),
        "maximum": _rounded(np.max(samples)),
        "quantiles": quantiles,
        "outlier_count": int(np.count_nonzero(outlier_mask)),
        "outlier_ratio": _rounded(np.mean(outlier_mask)),
        "clipping_count": int(np.count_nonzero(clipping_mask)),
        "clipping_ratio": _rounded(np.mean(clipping_mask)),
        "flatline_count": flatline_count,
        "flatline_seconds": flatline_seconds,
        "spectral_energy_total": spectral_energy,
        "dominant_frequency_hz": dominant_frequency,
        "spectral_bands": spectral_bands,
    }
    return features, anomaly_index


def calculate_sequence_continuity(db: Session, wav: WavFile) -> dict:
    rows = (
        db.query(
            SensorReading.source_sequence,
            SensorReading.source_dropped_samples_total,
        )
        .filter(
            SensorReading.sensor_id == wav.sensor_id,
            SensorReading.timestamp >= wav.started_at,
            SensorReading.timestamp <= wav.ended_at,
            SensorReading.source_sequence.isnot(None),
        )
        .order_by(SensorReading.timestamp)
        .all()
    )
    sequences: list[int] = []
    dropped_totals: list[int] = []
    for sequence, dropped_total in rows:
        value = int(sequence)
        if not sequences or sequences[-1] != value:
            sequences.append(value)
        if dropped_total is not None:
            dropped = int(dropped_total)
            if not dropped_totals or dropped_totals[-1] != dropped:
                dropped_totals.append(dropped)

    gaps = 0
    missing = 0
    resets = 0
    for previous, current in zip(sequences, sequences[1:], strict=False):
        delta = (current - previous) % _SEQUENCE_MODULUS
        if delta in (0, 1):
            continue
        if delta < _SEQUENCE_MODULUS // 2:
            gaps += 1
            missing += delta - 1
        else:
            resets += 1

    dropped_delta = None
    if len(dropped_totals) >= 2 and dropped_totals[-1] >= dropped_totals[0]:
        dropped_delta = dropped_totals[-1] - dropped_totals[0]
    return {
        "sequence_observations": len(sequences),
        "sequence_gap_count": gaps,
        "sequence_missing_count": missing,
        "sequence_reset_count": resets,
        "source_dropped_samples_delta": dropped_delta,
    }


def calculate_source_quality(db: Session, wav: WavFile) -> dict[str, int]:
    columns = (
        SensorReading.quality_valid_count,
        SensorReading.quality_lead_off_count,
        SensorReading.quality_rail_high_count,
        SensorReading.quality_rail_low_count,
        SensorReading.quality_jump_count,
        SensorReading.quality_recovery_count,
    )
    values = (
        db.query(*(func.coalesce(func.sum(column), 0) for column in columns))
        .filter(
            SensorReading.sensor_id == wav.sensor_id,
            SensorReading.timestamp >= wav.started_at,
            SensorReading.timestamp <= wav.ended_at,
        )
        .one()
    )
    return {
        name: int(value)
        for name, value in zip(
            ("valid", "lead_off", "rail_high", "rail_low", "jump", "recovery"),
            values,
            strict=True,
        )
    }


def _signal_classification(
    features: dict,
    continuity: dict,
    source_quality: dict[str, int],
    coverage_ratio: float,
) -> dict:
    technical_reasons: list[str] = []
    technical_score = 0.0
    technical_checks = (
        (coverage_ratio < _PARAMETERS["technical_coverage_minimum"], "low_coverage", 0.35),
        (
            features["clipping_ratio"] > _PARAMETERS["technical_clipping_ratio"],
            "clipping",
            0.25,
        ),
        (features["flatline_seconds"] > 5.0, "flatline", 0.35),
        (continuity["sequence_gap_count"] > 0, "sequence_gaps", 0.3),
        ((continuity["source_dropped_samples_delta"] or 0) > 0, "dropped_samples", 0.3),
        (source_quality["lead_off"] > 0, "lead_off", 0.3),
        (source_quality["rail_high"] + source_quality["rail_low"] > 0, "rail_flags", 0.25),
        (source_quality["jump"] > 0, "jump_flags", 0.15),
        (source_quality["recovery"] > 0, "recovery_flags", 0.15),
    )
    for triggered, reason, weight in technical_checks:
        if triggered:
            technical_reasons.append(reason)
            technical_score += weight

    biological_reasons: list[str] = []
    biological_score = 0.0
    if (
        not technical_reasons
        and features["outlier_ratio"] > _PARAMETERS["biological_outlier_ratio"]
    ):
        biological_reasons.append("robust_outliers")
        biological_score = min(1.0, features["outlier_ratio"] * 100.0)

    return {
        "data_quality_status": "technical_fault" if technical_reasons else "valid",
        "technical_fault_score": _rounded(min(1.0, technical_score)),
        "technical_fault_reasons": technical_reasons,
        "biological_candidate_score": _rounded(biological_score),
        "biological_candidate_reasons": biological_reasons,
        "is_anomaly": bool(biological_reasons),
        "anomaly_score": _rounded(biological_score),
        "anomaly_reasons": biological_reasons,
    }


def _feature_checksum(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _persisted_feature_payload(feature: WavFeature) -> dict:
    names = (
        "extractor_version",
        "calibration_version",
        "parameter_hash",
        "source_sha256",
        "sample_rate",
        "sample_count",
        "duration_seconds",
        "value_unit",
        "mean",
        "median",
        "rms",
        "standard_deviation",
        "minimum",
        "maximum",
        "quantiles",
        "outlier_count",
        "outlier_ratio",
        "clipping_count",
        "clipping_ratio",
        "data_quality_status",
        "technical_fault_score",
        "technical_fault_reasons",
        "biological_candidate_score",
        "biological_candidate_reasons",
        "source_quality_counts",
        "flatline_count",
        "flatline_seconds",
        "spectral_energy_total",
        "dominant_frequency_hz",
        "spectral_bands",
        "coverage_ratio",
        "timing_status",
        "missing_duration_seconds",
        "sequence_observations",
        "sequence_gap_count",
        "sequence_missing_count",
        "sequence_reset_count",
        "source_dropped_samples_delta",
        "is_anomaly",
        "anomaly_score",
        "anomaly_reasons",
    )
    return {name: getattr(feature, name) for name in names}


def _snapshot_feature_version(db: Session, feature: WavFeature) -> None:
    """Persist a verified feature identity exactly once."""
    if not feature.verified_at:
        return
    identity = {
        "wav_file_id": feature.wav_file_id,
        "extractor_version": feature.extractor_version,
        "calibration_version": feature.calibration_version,
        "parameter_hash": feature.parameter_hash,
    }
    if db.query(WavFeatureVersion.id).filter_by(**identity).first():
        return
    payload = _persisted_feature_payload(feature)
    if _feature_checksum(payload) != feature.feature_checksum:
        legacy_names = {
            "calibration_version",
            "parameter_hash",
            "data_quality_status",
            "technical_fault_score",
            "technical_fault_reasons",
            "biological_candidate_score",
            "biological_candidate_reasons",
            "source_quality_counts",
        }
        payload = {key: value for key, value in payload.items() if key not in legacy_names}
    if _feature_checksum(payload) != feature.feature_checksum:
        raise RuntimeError("Cannot version an unverified feature payload")
    db.add(
        WavFeatureVersion(
            **identity,
            source_sha256=feature.source_sha256,
            feature_checksum=feature.feature_checksum,
            feature_payload=payload,
            verified_at=feature.verified_at,
        )
    )
    db.flush()


def _write_anomaly_clip(
    samples: np.ndarray,
    sample_rate: int,
    center_index: int,
    destination: Path,
) -> None:
    clip_samples = settings.wav_anomaly_clip_seconds * sample_rate
    start = max(0, center_index - clip_samples // 2)
    end = min(samples.size, start + clip_samples)
    start = max(0, end - clip_samples)
    with wave.open(str(destination), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(samples[start:end].astype("<i2").tobytes())


def _encode_flac(source: Path, destination: Path) -> None:
    encoder = shutil.which("flac")
    if encoder is None:
        raise RuntimeError("FLAC archive enabled but flac encoder is unavailable")
    subprocess.run(  # noqa: S603 -- executable is resolved from the trusted system PATH
        [
            encoder,
            "--silent",
            "--verify",
            "--force",
            "--output-name",
            str(destination),
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )


def _upload_path(path: Path, key: str, content_type: str) -> tuple[str, int]:
    digest = _sha256_file(path)
    with path.open("rb") as artifact:
        size = wav_service.upload_artifact(
            artifact,
            key,
            content_type=content_type,
            content_sha256=digest,
        )
    return digest, size


def extract_and_verify_wav_features(db: Session, wav: WavFile) -> WavFeature:
    """Extract features and verify all configured archives before retention."""
    now = datetime.now(UTC)
    with tempfile.TemporaryDirectory(prefix="greenmind-wav-") as temporary_directory:
        directory = Path(temporary_directory)
        source_path = directory / "source.wav"
        with source_path.open("w+b") as source:
            wav_service.download_object(wav.s3_key, source)

        if source_path.stat().st_size != wav.file_size_bytes:
            raise ValueError("WAV object size differs from upload metadata")
        source_sha256 = _sha256_file(source_path)
        if wav.content_sha256 and source_sha256 != wav.content_sha256:
            raise ValueError("WAV checksum verification failed")

        raw_samples, sample_rate = _read_pcm_wav(source_path)
        if sample_rate != wav.sample_rate:
            raise ValueError("WAV sample rate differs from upload metadata")
        duration_seconds = raw_samples.size / sample_rate
        if abs(duration_seconds - wav.duration_seconds) > max(0.01, 1.0 / sample_rate):
            raise ValueError("WAV duration differs from upload metadata")

        samples = raw_samples * wav.pcm_scale_mv + wav.pcm_offset_mv
        clipping_bounds = (
            wav.pcm_offset_mv,
            32_767.0 * wav.pcm_scale_mv + wav.pcm_offset_mv,
        )
        signal_features, anomaly_index = calculate_signal_features(
            samples, sample_rate, clipping_bounds=clipping_bounds
        )
        continuity = calculate_sequence_continuity(db, wav)
        source_quality = calculate_source_quality(db, wav)
        declared_seconds = max(0.0, (wav.ended_at - wav.started_at).total_seconds())
        missing_seconds = _rounded(max(0.0, declared_seconds - duration_seconds))
        classification = _signal_classification(
            signal_features, continuity, source_quality, wav.coverage_ratio
        )
        feature_values = {
            "extractor_version": EXTRACTOR_VERSION,
            "calibration_version": wav.calibration_version,
            "parameter_hash": PARAMETER_HASH,
            "source_sha256": source_sha256,
            "sample_rate": sample_rate,
            "sample_count": int(samples.size),
            "duration_seconds": _rounded(duration_seconds),
            "value_unit": "mV",
            **signal_features,
            "coverage_ratio": _rounded(wav.coverage_ratio),
            "timing_status": wav.timing_status,
            "missing_duration_seconds": missing_seconds,
            **continuity,
            "source_quality_counts": source_quality,
            **classification,
        }
        checksum = _feature_checksum(feature_values)

        if wav.feature is not None:
            _snapshot_feature_version(db, wav.feature)
        feature = wav.feature or WavFeature(
            wav_file_id=wav.id,
            sensor_id=wav.sensor_id,
            gateway_id=wav.gateway_id,
            sensor_mac=wav.sensor_mac,
            started_at=wav.started_at,
            ended_at=wav.ended_at,
        )
        for name, value in feature_values.items():
            setattr(feature, name, value)
        feature.feature_checksum = checksum
        feature.verified_at = None
        db.add(feature)
        wav.content_sha256 = source_sha256
        db.commit()

        mac_clean = wav.sensor_mac.replace(":", "").upper()
        date_path = wav.started_at.strftime("%Y%m%d")
        if classification["is_anomaly"] and settings.wav_anomaly_archive_enabled:
            clip_path = directory / "anomaly.wav"
            _write_anomaly_clip(raw_samples, sample_rate, anomaly_index, clip_path)
            artifact_path = clip_path
            extension = "wav"
            content_type = "audio/wav"
            if settings.wav_flac_archive_enabled:
                artifact_path = directory / "anomaly.flac"
                _encode_flac(clip_path, artifact_path)
                extension = "flac"
                content_type = "audio/flac"
            key = f"anomaly/{mac_clean}/{date_path}/{wav.id}_{checksum[:12]}.{extension}"
            digest, size = _upload_path(artifact_path, key, content_type)
            feature.anomaly_s3_key = key
            feature.anomaly_sha256 = digest
            feature.anomaly_file_size_bytes = size
            feature.anomaly_verified_at = now
            feature.anomaly_expires_at = now + timedelta(days=settings.retention_anomaly_days)
            db.commit()

        if settings.wav_flac_archive_enabled:
            flac_path = directory / "source.flac"
            _encode_flac(source_path, flac_path)
            key = f"flac/{mac_clean}/{date_path}/{wav.id}_{source_sha256[:12]}.flac"
            digest, size = _upload_path(flac_path, key, "audio/flac")
            feature.flac_s3_key = key
            feature.flac_sha256 = digest
            feature.flac_file_size_bytes = size
            feature.flac_verified_at = now
            feature.flac_expires_at = now + timedelta(days=settings.retention_flac_days)
            db.commit()

        db.refresh(feature)
        if _feature_checksum(_persisted_feature_payload(feature)) != feature.feature_checksum:
            raise RuntimeError("Persisted WAV feature verification failed")
        feature.verified_at = now
        wav.feature_status = "verified"
        wav.feature_attempts = 0
        wav.feature_error = None
        wav.feature_verified_at = now
        db.flush()
        _snapshot_feature_version(db, feature)
        db.commit()
        db.refresh(feature)
        return feature


def _next_candidate(db: Session, now: datetime) -> WavFile | None:
    stale = now - timedelta(hours=1)
    candidate_states = [
        WavFile.feature_status.in_(("pending", "failed")),
        (WavFile.feature_status == "processing") & (WavFile.feature_started_at < stale),
        and_(
            WavFile.feature_status == "verified",
            ~WavFile.feature.has(
                and_(
                    WavFeature.extractor_version == EXTRACTOR_VERSION,
                    WavFeature.parameter_hash == PARAMETER_HASH,
                    WavFeature.calibration_version == WavFile.calibration_version,
                )
            ),
        ),
    ]
    if settings.wav_flac_archive_enabled:
        candidate_states.append(
            and_(
                WavFile.feature_status == "verified",
                ~WavFile.feature.has(
                    WavFeature.flac_s3_key.isnot(None) & WavFeature.flac_verified_at.isnot(None)
                ),
            )
        )
    if settings.wav_anomaly_archive_enabled:
        candidate_states.append(
            and_(
                WavFile.feature_status == "verified",
                WavFile.feature.has(WavFeature.is_anomaly.is_(True)),
                ~WavFile.feature.has(
                    WavFeature.anomaly_s3_key.isnot(None)
                    & WavFeature.anomaly_verified_at.isnot(None)
                ),
            )
        )
    return (
        db.query(WavFile)
        .filter(
            WavFile.raw_deleted_at.is_(None),
            WavFile.feature_attempts < settings.wav_feature_max_attempts,
            or_(*candidate_states),
        )
        .order_by(WavFile.started_at)
        .with_for_update(skip_locked=True)
        .first()
    )


def process_pending_wav_features(db: Session) -> dict[str, int]:
    """Process a bounded retryable batch, committing each verified file."""
    result = {"verified": 0, "failed": 0}
    for _ in range(settings.wav_feature_batch_size):
        now = datetime.now(UTC)
        wav = _next_candidate(db, now)
        if wav is None:
            break
        wav.feature_status = "processing"
        wav.feature_started_at = now
        wav.feature_attempts += 1
        db.commit()
        wav_id = wav.id
        try:
            extract_and_verify_wav_features(db, wav)
            result["verified"] += 1
        except Exception as exc:
            db.rollback()
            failed = db.query(WavFile).filter(WavFile.id == wav_id).first()
            if failed is not None:
                failed.feature_status = "failed"
                failed.feature_error = f"{type(exc).__name__}: {exc}"[:500]
                db.commit()
            result["failed"] += 1
            logger.exception("WAV feature extraction failed for %s", wav_id)
    return result
