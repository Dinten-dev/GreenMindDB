"""WAV file management endpoints — upload from gateways, list, download, bundle."""

import logging
import uuid
from datetime import datetime
from io import BytesIO

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.auth import get_current_user, verify_password
from app.database import get_db
from app.models.master import Gateway, Sensor, Zone
from app.models.user import User
from app.models.wav_file import WavFile
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
    db: Session = Depends(get_db),
    x_api_key: str | None = Header(None),
):
    """Receive a WAV file from a gateway and store in MinIO.

    Authenticated via X-Api-Key (same as /ingest).
    """
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing X-Api-Key header")

    # Authenticate gateway
    gateway = db.query(Gateway).filter(Gateway.hardware_id == gateway_serial).first()
    if not gateway or not gateway.api_key_hash:
        raise HTTPException(status_code=403, detail="Unknown or unconfigured gateway")
    if not verify_password(x_api_key, gateway.api_key_hash):
        raise HTTPException(status_code=401, detail="Invalid API key")

    # Parse timestamps
    try:
        start_dt = datetime.fromisoformat(started_at)
        end_dt = datetime.fromisoformat(ended_at)
    except ValueError as e:
        raise HTTPException(
            status_code=400, detail="Invalid timestamp format (ISO 8601 required)"
        ) from e

    # Validate timestamp_source
    if timestamp_source not in ("filename", "embedded"):
        timestamp_source = "filename"

    # Read file
    file_data = await file.read()
    file_size = len(file_data)
    file_io = BytesIO(file_data)

    # Extract WAV metadata for validation
    try:
        meta = wav_service.extract_wav_metadata(file_io)
    except Exception as e:
        raise HTTPException(status_code=400, detail="Invalid WAV file") from e

    # Upload to MinIO
    s3_key = wav_service.upload_wav(
        file_data=file_io,
        sensor_mac=sensor_mac,
        started_at=start_dt,
        file_size=file_size,
    )

    # Find or identify sensor
    sensor = db.query(Sensor).filter(Sensor.mac_address == sensor_mac).first()
    sensor_id = sensor.id if sensor else uuid.uuid4()

    # Create DB record
    wav_record = WavFile(
        sensor_id=sensor_id,
        gateway_id=gateway.id,
        sensor_mac=sensor_mac,
        s3_key=s3_key,
        sample_rate=meta.get("sample_rate", sample_rate),
        duration_seconds=meta.get("duration_seconds", 0.0),
        file_size_bytes=file_size,
        started_at=start_dt,
        ended_at=end_dt,
        timestamp_source=timestamp_source,
    )
    db.add(wav_record)
    db.commit()
    db.refresh(wav_record)

    logger.info(
        "WAV uploaded: sensor=%s s3=%s duration=%.1fs source=%s",
        sensor_mac,
        s3_key,
        wav_record.duration_seconds,
        timestamp_source,
    )

    return {
        "status": "ok",
        "wav_id": str(wav_record.id),
        "s3_key": s3_key,
        "duration_seconds": wav_record.duration_seconds,
    }


@router.get("/files")
def list_wav_files(
    sensor_id: str | None = None,
    sensor_mac: str | None = None,
    from_dt: str | None = None,
    to_dt: str | None = None,
    limit: int = 100,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List available WAV files with optional filters (scoped to organization)."""
    if not current_user.organization_id:
        return []

    # Join through ownership chain: WavFile → Sensor → Gateway → Zone
    query = (
        db.query(WavFile)
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

    files = query.order_by(desc(WavFile.started_at)).limit(min(limit, 10_000)).all()

    return [
        {
            "id": str(f.id),
            "sensor_mac": f.sensor_mac,
            "sensor_id": str(f.sensor_id),
            "sample_rate": f.sample_rate,
            "duration_seconds": f.duration_seconds,
            "file_size_bytes": f.file_size_bytes,
            "started_at": f.started_at.isoformat(),
            "ended_at": f.ended_at.isoformat(),
            "created_at": f.created_at.isoformat(),
            "timestamp_source": f.timestamp_source,
        }
        for f in files
    ]


@router.get("/count")
def count_wav_files(
    sensor_id: str,
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
    wav_id: str,
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

    body = wav_service.download_wav_bytes(wav_file.s3_key)
    sensor_name = _resolve_sensor_name(wav_file.sensor_id, db)
    filename = wav_service.generate_download_filename(
        sensor_name=sensor_name,
        started_at=wav_file.started_at,
    )

    return StreamingResponse(
        iter([body]),
        media_type="audio/wav",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(body)),
        },
    )


@router.get("/download-bundle")
def download_wav_bundle(
    sensor_id: str,
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
            WavFile.started_at >= start_dt,
            WavFile.ended_at <= end_dt,
            Zone.organization_id == current_user.organization_id,
        )
        .order_by(WavFile.started_at)
        .limit(10_000)
        .all()
    )

    if not wav_files:
        raise HTTPException(
            status_code=404,
            detail="No WAV files found for this sensor in the given time range",
        )

    sensor_name = _resolve_sensor_name(uuid.UUID(sensor_id), db)

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

    total_size = sum(f.file_size_bytes for f in wav_files)
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
