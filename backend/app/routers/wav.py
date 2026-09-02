"""WAV file management endpoints — upload from gateways, list, download, bundle."""

import hashlib
import logging
import math
import tempfile
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import desc, func
from sqlalchemy.orm import Session, joinedload

from app.auth import get_current_user
from app.config import settings
from app.database import get_db
from app.gateway_auth import get_current_gateway
from app.models.master import Gateway, Sensor, Zone
from app.models.user import User
from app.models.wav_file import WavFeature, WavFile
from app.services import wav_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/wav", tags=["wav"])


@router.post("/upload", status_code=201)
async def upload_wav(
    file: UploadFile = File(...),
    sensor_mac: str = Form(...),
    gateway_serial: str = Form(...),
    sample_rate: int = Form(380),
    started_at: str = Form(...),
    ended_at: str = Form(...),
    timestamp_source: str = Form("filename"),
    pcm_encoding_version: str = Form("unsigned-mv-linear-int16-v1"),
    pcm_scale_mv: float = Form(3300.0 / 32767.0),
    pcm_offset_mv: float = Form(0.0),
    calibration_version: str = Form("nominal-adc-3v3-v1"),
    gateway: Gateway = Depends(get_current_gateway),
    db: Session = Depends(get_db),
):
    """Receive a WAV file from a gateway and store in MinIO.

    Authenticated via X-Api-Key (same as /ingest).
    """
    if len(gateway_serial) > 100 or len(sensor_mac) > 17:
        raise HTTPException(status_code=422, detail="Gateway or sensor identifier is too long")
    if not 1 <= sample_rate <= 192_000:
        raise HTTPException(status_code=422, detail="Invalid WAV sample rate")
    if gateway.hardware_id != gateway_serial:
        raise HTTPException(status_code=403, detail="Gateway identity mismatch")
    if pcm_encoding_version != "unsigned-mv-linear-int16-v1":
        raise HTTPException(status_code=422, detail="Unsupported PCM encoding version")
    if not math.isfinite(pcm_scale_mv) or not 0 < pcm_scale_mv <= 10:
        raise HTTPException(status_code=422, detail="Invalid PCM scale")
    if not math.isfinite(pcm_offset_mv) or abs(pcm_offset_mv) > 10_000:
        raise HTTPException(status_code=422, detail="Invalid PCM offset")
    if not 1 <= len(calibration_version) <= 50:
        raise HTTPException(status_code=422, detail="Invalid calibration version")

    try:
        from app.validation import normalize_mac_address

        sensor_mac = normalize_mac_address(sensor_mac)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid sensor MAC address") from exc

    sensor = (
        db.query(Sensor)
        .filter(Sensor.mac_address == sensor_mac, Sensor.gateway_id == gateway.id)
        .first()
    )
    if not sensor:
        raise HTTPException(status_code=403, detail="Sensor is not assigned to this gateway")

    # Parse timestamps
    try:
        start_dt = datetime.fromisoformat(started_at)
        end_dt = datetime.fromisoformat(ended_at)
    except ValueError as e:
        raise HTTPException(
            status_code=400, detail="Invalid timestamp format (ISO 8601 required)"
        ) from e
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=UTC)
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=UTC)
    # Validate timestamp_source
    if timestamp_source not in ("filename", "embedded"):
        raise HTTPException(status_code=422, detail="Invalid timestamp source")

    file_io = tempfile.SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b")
    file_size = 0
    hasher = hashlib.sha256()
    try:
        while chunk := await file.read(64 * 1024):
            file_size += len(chunk)
            if file_size > settings.max_wav_upload_bytes:
                raise HTTPException(status_code=413, detail="WAV file exceeds upload limit")
            hasher.update(chunk)
            file_io.write(chunk)
        if file_size == 0:
            raise HTTPException(status_code=422, detail="Empty WAV file")
        file_io.seek(0)

        try:
            meta = wav_service.extract_wav_metadata(file_io)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Invalid WAV file") from exc
        if meta["sample_rate"] != sample_rate:
            raise HTTPException(status_code=422, detail="WAV sample rate does not match metadata")
        try:
            end_dt, coverage_ratio, timing_status = wav_service.reconcile_wav_timing(
                start_dt,
                end_dt,
                meta["duration_seconds"],
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        content_sha256 = hasher.hexdigest()
        s3_key = wav_service.build_wav_s3_key(sensor_mac, start_dt, content_sha256)
        existing = (
            db.query(WavFile)
            .filter(
                WavFile.s3_key == s3_key,
                WavFile.gateway_id == gateway.id,
                WavFile.sensor_id == sensor.id,
            )
            .first()
        )
        if existing:
            return {
                "status": "duplicate",
                "wav_id": str(existing.id),
                "s3_key": existing.s3_key,
                "duration_seconds": existing.duration_seconds,
                "coverage_ratio": existing.coverage_ratio,
                "timing_status": existing.timing_status,
                "feature_status": existing.feature_status,
                "raw_available": existing.raw_deleted_at is None,
            }

        wav_service.upload_wav(
            file_data=file_io,
            sensor_mac=sensor_mac,
            started_at=start_dt,
            file_size=file_size,
            content_sha256=content_sha256,
        )
    finally:
        file_io.close()

    # Create DB record
    wav_record = WavFile(
        sensor_id=sensor.id,
        gateway_id=gateway.id,
        sensor_mac=sensor_mac,
        s3_key=s3_key,
        content_sha256=content_sha256,
        sample_rate=meta.get("sample_rate", sample_rate),
        duration_seconds=meta.get("duration_seconds", 0.0),
        coverage_ratio=coverage_ratio,
        timing_status=timing_status,
        file_size_bytes=file_size,
        started_at=start_dt,
        ended_at=end_dt,
        timestamp_source=timestamp_source,
        pcm_encoding_version=pcm_encoding_version,
        pcm_scale_mv=pcm_scale_mv,
        pcm_offset_mv=pcm_offset_mv,
        calibration_version=calibration_version,
    )
    db.add(wav_record)
    db.commit()
    db.refresh(wav_record)

    logger.info(
        "WAV uploaded: sensor=%s s3=%s duration=%.1fs source=%s timing=%s coverage=%.3f",
        sensor_mac,
        s3_key,
        wav_record.duration_seconds,
        timestamp_source,
        timing_status,
        coverage_ratio,
    )

    return {
        "status": "ok",
        "wav_id": str(wav_record.id),
        "s3_key": s3_key,
        "duration_seconds": wav_record.duration_seconds,
        "coverage_ratio": wav_record.coverage_ratio,
        "timing_status": wav_record.timing_status,
        "feature_status": wav_record.feature_status,
        "raw_available": True,
    }


@router.get("/files")
def list_wav_files(
    sensor_id: uuid.UUID | None = None,
    sensor_mac: str | None = None,
    from_dt: str | None = None,
    to_dt: str | None = None,
    limit: int = Query(100, ge=1, le=1_000),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List available WAV files with optional filters (scoped to organization)."""
    if not current_user.organization_id:
        return []

    # Join through ownership chain: WavFile → Sensor → Gateway → Zone
    query = (
        db.query(WavFile)
        .options(joinedload(WavFile.feature))
        .join(Sensor, Sensor.id == WavFile.sensor_id)
        .join(Gateway, Gateway.id == Sensor.gateway_id)
        .join(Zone, Zone.id == Gateway.zone_id)
        .filter(Zone.organization_id == current_user.organization_id)
    )

    if sensor_id:
        query = query.filter(WavFile.sensor_id == sensor_id)
    if sensor_mac:
        query = query.filter(WavFile.sensor_mac == sensor_mac)
    if from_dt:
        try:
            query = query.filter(WavFile.started_at >= datetime.fromisoformat(from_dt))
        except ValueError as e:
            raise HTTPException(status_code=400, detail="Invalid from_dt format") from e
    if to_dt:
        try:
            query = query.filter(WavFile.ended_at <= datetime.fromisoformat(to_dt))
        except ValueError as e:
            raise HTTPException(status_code=400, detail="Invalid to_dt format") from e

    files = query.order_by(desc(WavFile.started_at)).limit(limit).all()

    return [
        {
            "id": str(f.id),
            "sensor_mac": f.sensor_mac,
            "sensor_id": str(f.sensor_id),
            "sample_rate": f.sample_rate,
            "duration_seconds": f.duration_seconds,
            "coverage_ratio": f.coverage_ratio,
            "timing_status": f.timing_status,
            "file_size_bytes": f.file_size_bytes,
            "started_at": f.started_at.isoformat(),
            "ended_at": f.ended_at.isoformat(),
            "created_at": f.created_at.isoformat(),
            "timestamp_source": f.timestamp_source,
            "raw_available": f.raw_deleted_at is None,
            "raw_deleted_at": f.raw_deleted_at.isoformat() if f.raw_deleted_at else None,
            "feature_status": f.feature_status,
            "feature_verified_at": (
                f.feature_verified_at.isoformat() if f.feature_verified_at else None
            ),
            "is_anomaly": f.feature.is_anomaly if f.feature else None,
            "anomaly_score": f.feature.anomaly_score if f.feature else None,
            "flac_available": bool(f.feature and f.feature.flac_s3_key),
        }
        for f in files
    ]


@router.get("/count")
def count_wav_files(
    sensor_id: uuid.UUID,
    from_dt: str | None = None,
    to_dt: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return count and total size of WAV files matching filters.

    Useful for the frontend to estimate ZIP download size before
    triggering a potentially large bundle download.
    """
    if not current_user.organization_id:
        return {"count": 0, "total_bytes": 0}

    query = (
        db.query(
            func.count(WavFile.id).label("count"),
            func.coalesce(func.sum(WavFile.file_size_bytes), 0).label("total_bytes"),
        )
        .join(Sensor, Sensor.id == WavFile.sensor_id)
        .join(Gateway, Gateway.id == Sensor.gateway_id)
        .join(Zone, Zone.id == Gateway.zone_id)
        .filter(
            WavFile.sensor_id == sensor_id,
            WavFile.raw_deleted_at.is_(None),
            Zone.organization_id == current_user.organization_id,
        )
    )

    if from_dt:
        try:
            query = query.filter(WavFile.started_at >= datetime.fromisoformat(from_dt))
        except ValueError as e:
            raise HTTPException(status_code=400, detail="Invalid from_dt format") from e
    if to_dt:
        try:
            query = query.filter(WavFile.ended_at <= datetime.fromisoformat(to_dt))
        except ValueError as e:
            raise HTTPException(status_code=400, detail="Invalid to_dt format") from e

    row = query.one()
    return {"count": row.count, "total_bytes": row.total_bytes}


@router.get("/features")
def list_wav_features(
    sensor_id: uuid.UUID | None = None,
    from_dt: str | None = None,
    to_dt: str | None = None,
    anomalies_only: bool = False,
    limit: int = Query(100, ge=1, le=1_000),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List verified long-lived WAV features scoped to the organization."""
    if not current_user.organization_id:
        return []
    query = (
        db.query(WavFeature)
        .join(Sensor, Sensor.id == WavFeature.sensor_id)
        .join(Gateway, Gateway.id == Sensor.gateway_id)
        .join(Zone, Zone.id == Gateway.zone_id)
        .filter(
            Zone.organization_id == current_user.organization_id,
            WavFeature.verified_at.isnot(None),
        )
    )
    if sensor_id:
        query = query.filter(WavFeature.sensor_id == sensor_id)
    if from_dt:
        try:
            query = query.filter(WavFeature.started_at >= datetime.fromisoformat(from_dt))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid from_dt format") from exc
    if to_dt:
        try:
            query = query.filter(WavFeature.ended_at <= datetime.fromisoformat(to_dt))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid to_dt format") from exc
    if anomalies_only:
        query = query.filter(WavFeature.is_anomaly.is_(True))

    features = query.order_by(desc(WavFeature.started_at)).limit(limit).all()
    return [
        {
            "id": str(feature.id),
            "wav_file_id": str(feature.wav_file_id),
            "sensor_id": str(feature.sensor_id),
            "sensor_mac": feature.sensor_mac,
            "started_at": feature.started_at.isoformat(),
            "ended_at": feature.ended_at.isoformat(),
            "extractor_version": feature.extractor_version,
            "feature_checksum": feature.feature_checksum,
            "sample_rate": feature.sample_rate,
            "sample_count": feature.sample_count,
            "duration_seconds": feature.duration_seconds,
            "value_unit": feature.value_unit,
            "mean": feature.mean,
            "median": feature.median,
            "rms": feature.rms,
            "standard_deviation": feature.standard_deviation,
            "minimum": feature.minimum,
            "maximum": feature.maximum,
            "quantiles": feature.quantiles,
            "outlier_count": feature.outlier_count,
            "outlier_ratio": feature.outlier_ratio,
            "clipping_count": feature.clipping_count,
            "clipping_ratio": feature.clipping_ratio,
            "coverage_ratio": feature.coverage_ratio,
            "timing_status": feature.timing_status,
            "missing_duration_seconds": feature.missing_duration_seconds,
            "flatline_count": feature.flatline_count,
            "flatline_seconds": feature.flatline_seconds,
            "sequence_observations": feature.sequence_observations,
            "sequence_gap_count": feature.sequence_gap_count,
            "sequence_missing_count": feature.sequence_missing_count,
            "sequence_reset_count": feature.sequence_reset_count,
            "source_dropped_samples_delta": feature.source_dropped_samples_delta,
            "spectral_energy_total": feature.spectral_energy_total,
            "dominant_frequency_hz": feature.dominant_frequency_hz,
            "spectral_bands": feature.spectral_bands,
            "is_anomaly": feature.is_anomaly,
            "anomaly_score": feature.anomaly_score,
            "anomaly_reasons": feature.anomaly_reasons,
            "anomaly_archive_available": bool(feature.anomaly_s3_key),
            "flac_archive_available": bool(feature.flac_s3_key),
            "verified_at": feature.verified_at.isoformat(),
        }
        for feature in features
    ]


def _resolve_sensor_name(sensor_id: uuid.UUID, db: Session) -> str:
    """Resolve sensor ID to a human-readable name for filenames."""
    sensor = db.query(Sensor).filter(Sensor.id == sensor_id).first()
    if sensor and sensor.name:
        return sensor.name
    if sensor and sensor.mac_address:
        return sensor.mac_address
    return "sensor"


@router.get("/download/{wav_id}")
def download_wav(
    wav_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Stream a WAV file download proxied through the backend.

    MinIO is only accessible on the Docker network, so we proxy the
    download instead of returning a presigned URL that the browser
    cannot reach.
    """
    if not current_user.organization_id:
        raise HTTPException(status_code=403, detail="No organization")

    # Verify ownership: WavFile → Sensor → Gateway → Zone → Organization
    wav_file = (
        db.query(WavFile)
        .join(Sensor, Sensor.id == WavFile.sensor_id)
        .join(Gateway, Gateway.id == Sensor.gateway_id)
        .join(Zone, Zone.id == Gateway.zone_id)
        .filter(
            WavFile.id == wav_id,
            Zone.organization_id == current_user.organization_id,
        )
        .first()
    )
    if not wav_file:
        raise HTTPException(status_code=404, detail="WAV file not found")
    if wav_file.raw_deleted_at is not None:
        raise HTTPException(status_code=410, detail="Raw WAV expired; verified features remain")

    sensor_name = _resolve_sensor_name(wav_file.sensor_id, db)
    filename = wav_service.generate_download_filename(
        sensor_name=sensor_name,
        started_at=wav_file.started_at,
    )

    return StreamingResponse(
        wav_service.stream_wav_bytes(wav_file.s3_key),
        media_type="audio/wav",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(wav_file.file_size_bytes),
        },
    )


@router.get("/download-bundle")
def download_wav_bundle(
    sensor_id: uuid.UUID,
    from_dt: str,
    to_dt: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Download multiple WAV files as a ZIP bundle for a time range.

    Streams the ZIP progressively from MinIO. Files are named with
    the standardized greenmind_{sensor}_{timestamp}.wav format.
    """
    if not current_user.organization_id:
        raise HTTPException(status_code=403, detail="No organization")

    try:
        start_dt = datetime.fromisoformat(from_dt)
        end_dt = datetime.fromisoformat(to_dt)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid date format") from e

    # Query WAV files in range, scoped to organization
    wav_files = (
        db.query(WavFile)
        .join(Sensor, Sensor.id == WavFile.sensor_id)
        .join(Gateway, Gateway.id == Sensor.gateway_id)
        .join(Zone, Zone.id == Gateway.zone_id)
        .filter(
            WavFile.sensor_id == sensor_id,
            WavFile.raw_deleted_at.is_(None),
            WavFile.started_at >= start_dt,
            WavFile.ended_at <= end_dt,
            Zone.organization_id == current_user.organization_id,
        )
        .order_by(WavFile.started_at)
        .limit(settings.max_wav_bundle_files + 1)
        .all()
    )

    if not wav_files:
        raise HTTPException(
            status_code=404,
            detail="No WAV files found for this sensor in the given time range",
        )

    if len(wav_files) > settings.max_wav_bundle_files:
        raise HTTPException(status_code=413, detail="WAV bundle contains too many files")

    total_size = sum(f.file_size_bytes for f in wav_files)
    if total_size > settings.max_wav_bundle_bytes:
        raise HTTPException(status_code=413, detail="WAV bundle exceeds download limit")

    sensor_name = _resolve_sensor_name(sensor_id, db)

    # Build per-file names inside the ZIP
    s3_keys = [f.s3_key for f in wav_files]
    inner_filenames = [
        wav_service.generate_download_filename(
            sensor_name=sensor_name,
            started_at=f.started_at,
        )
        for f in wav_files
    ]

    # ZIP bundle filename
    bundle_name = wav_service.generate_download_filename(
        sensor_name=sensor_name,
        started_at=wav_files[0].started_at,
        ended_at=wav_files[-1].ended_at,
        extension="zip",
    )

    logger.info(
        "WAV bundle download: %d files, ~%.1f MB, sensor=%s",
        len(wav_files),
        total_size / (1024 * 1024),
        sensor_name,
    )

    return StreamingResponse(
        wav_service.stream_wav_zip(s3_keys, inner_filenames),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{bundle_name}"',
        },
    )
