"""Service layer for WAV file storage in MinIO/S3."""

import hashlib
import logging
import wave
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import BinaryIO

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from app.config import settings

logger = logging.getLogger(__name__)

# Lazy-initialized S3 client
_s3_client = None
_WAV_BUCKET = "greenmind-raw"


def reconcile_wav_timing(
    started_at: datetime,
    declared_ended_at: datetime,
    audio_duration_seconds: float,
    *,
    now: datetime | None = None,
) -> tuple[datetime, float, str]:
    """Reconcile legacy gateway timestamps with the WAV frame duration.

    Older gateways used file mtime as ``ended_at``. Sparse recordings therefore
    legitimately contain less audio than their wall-clock window. Invalid or too
    short declared windows are replaced with the duration derived from WAV frames.
    """
    if audio_duration_seconds <= 0 or audio_duration_seconds > timedelta(days=1).total_seconds():
        raise ValueError("Invalid WAV audio duration")

    now = now or datetime.now(UTC)
    inferred_end = started_at + timedelta(seconds=audio_duration_seconds)
    future_limit = now + timedelta(minutes=15)
    if inferred_end > future_limit:
        raise ValueError("WAV timestamp is too far in the future")

    declared_duration = (declared_ended_at - started_at).total_seconds()
    declared_valid = (
        0 < declared_duration <= timedelta(days=1).total_seconds()
        and declared_ended_at <= future_limit
    )
    if not declared_valid:
        return inferred_end, 1.0, "inferred"

    tolerance = max(1.0, declared_duration * 0.05)
    difference = audio_duration_seconds - declared_duration
    if abs(difference) <= tolerance:
        return declared_ended_at, 1.0, "complete"
    if difference < 0:
        coverage_ratio = max(0.0, min(1.0, audio_duration_seconds / declared_duration))
        return declared_ended_at, coverage_ratio, "partial"
    return inferred_end, 1.0, "inferred"


def _get_s3_client():
    """Get or create the S3/MinIO client."""
    global _s3_client
    if _s3_client is not None:
        return _s3_client

    _s3_client = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key,
        region_name=settings.s3_region,
        config=BotoConfig(signature_version="s3v4"),
    )

    # Ensure bucket exists
    try:
        _s3_client.head_bucket(Bucket=_WAV_BUCKET)
    except ClientError:
        logger.info("Creating S3 bucket: %s", _WAV_BUCKET)
        _s3_client.create_bucket(Bucket=_WAV_BUCKET)

    return _s3_client


def upload_wav(
    file_data: BinaryIO,
    sensor_mac: str,
    started_at: datetime,
    file_size: int,
    content_sha256: str | None = None,
) -> str:
    """Upload a WAV file to MinIO and return the S3 key.

    Key format: wav/{sensor_mac}/{YYYYMMDD}/{HHmmss}.wav
    """
    if content_sha256 is None:
        hasher = hashlib.sha256()
        file_data.seek(0)
        for chunk in iter(lambda: file_data.read(1024 * 1024), b""):
            hasher.update(chunk)
        content_sha256 = hasher.hexdigest()
        file_data.seek(0)

    s3_key = build_wav_s3_key(sensor_mac, started_at, content_sha256)
    client = _get_s3_client()
    client.upload_fileobj(
        file_data,
        _WAV_BUCKET,
        s3_key,
        ExtraArgs={"ContentType": "audio/wav"},
    )

    logger.info("Uploaded WAV object (%d bytes)", file_size)
    return s3_key


def build_wav_s3_key(sensor_mac: str, started_at: datetime, content_sha256: str) -> str:
    """Build a deterministic, collision-resistant object key for retry idempotency."""
    import zoneinfo

    switzerland_tz = zoneinfo.ZoneInfo("Europe/Zurich")
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=UTC)
    local_start = started_at.astimezone(switzerland_tz)

    date_str = local_start.strftime("%Y%m%d")
    time_str = local_start.strftime("%H%M%S")
    mac_clean = sensor_mac.replace(":", "").upper()
    return f"wav/{mac_clean}/{date_str}/{time_str}_{content_sha256}.wav"


def generate_presigned_url(s3_key: str, expires_in: int = 3600) -> str:
    """Generate a presigned download URL for a WAV file."""
    client = _get_s3_client()
    url = client.generate_presigned_url(
        "get_object",
        Params={"Bucket": _WAV_BUCKET, "Key": s3_key},
        ExpiresIn=expires_in,
    )
    return url


def stream_wav_bytes(s3_key: str) -> Generator[bytes, None, None]:
    """Yield a WAV object without buffering the complete file in application memory."""
    client = _get_s3_client()
    response = client.get_object(Bucket=_WAV_BUCKET, Key=s3_key)
    body = response["Body"]
    try:
        while chunk := body.read(64 * 1024):
            yield chunk
    finally:
        body.close()


def download_object(s3_key: str, destination: BinaryIO) -> None:
    """Download one raw or derived object into a seekable file."""
    client = _get_s3_client()
    destination.seek(0)
    destination.truncate(0)
    client.download_fileobj(_WAV_BUCKET, s3_key, destination)
    destination.seek(0)


def upload_artifact(
    file_data: BinaryIO,
    s3_key: str,
    *,
    content_type: str,
    content_sha256: str,
) -> int:
    """Upload and verify a derived lossless artifact."""
    client = _get_s3_client()
    file_data.seek(0, 2)
    size = file_data.tell()
    file_data.seek(0)
    client.upload_fileobj(
        file_data,
        _WAV_BUCKET,
        s3_key,
        ExtraArgs={
            "ContentType": content_type,
            "Metadata": {"sha256": content_sha256},
        },
    )
    head = client.head_object(Bucket=_WAV_BUCKET, Key=s3_key)
    if head.get("ContentLength") != size:
        raise RuntimeError("Artifact size verification failed")
    if head.get("Metadata", {}).get("sha256") != content_sha256:
        raise RuntimeError("Artifact checksum metadata verification failed")
    return size


def extract_wav_metadata(file_data: BinaryIO) -> dict:
    """Extract metadata from a WAV file (sample rate, duration, size).

    Returns dict with sample_rate, duration_seconds, n_samples.
    """
    file_data.seek(0)
    try:
        with wave.open(file_data, "rb") as wf:
            sample_rate = wf.getframerate()
            n_frames = wf.getnframes()
            channels = wf.getnchannels()
            sample_width = wf.getsampwidth()
            compression = wf.getcomptype()
            if channels != 1 or sample_width != 2 or compression != "NONE":
                raise ValueError("WAV must be mono, 16-bit, uncompressed PCM")
            if not 1 <= sample_rate <= 192_000 or n_frames <= 0:
                raise ValueError("WAV sample rate or frame count is invalid")
            bytes_per_frame = channels * sample_width
            frames_read = 0
            while frames_read < n_frames:
                block = wf.readframes(min(8_192, n_frames - frames_read))
                if not block or len(block) % bytes_per_frame:
                    break
                frames_read += len(block) // bytes_per_frame
            if frames_read != n_frames:
                raise ValueError("WAV payload is truncated")
            duration = n_frames / sample_rate if sample_rate > 0 else 0.0
            return {
                "sample_rate": sample_rate,
                "duration_seconds": round(duration, 2),
                "n_samples": n_frames,
                "channels": channels,
                "sample_width": sample_width,
            }
    finally:
        file_data.seek(0)


def delete_wav(s3_key: str) -> None:
    """Delete a WAV file from MinIO."""
    client = _get_s3_client()
    client.delete_object(Bucket=_WAV_BUCKET, Key=s3_key)
    logger.info("Deleted WAV s3://%s/%s", _WAV_BUCKET, s3_key)


def delete_artifact(s3_key: str) -> None:
    """Idempotently delete a derived WAV/FLAC artifact."""
    client = _get_s3_client()
    client.delete_object(Bucket=_WAV_BUCKET, Key=s3_key)
    logger.info("Deleted derived artifact s3://%s/%s", _WAV_BUCKET, s3_key)


def _sanitize_filename(name: str) -> str:
    """Convert a name to a filesystem-safe slug."""
    import re
    import unicodedata

    # Normalize unicode, strip accents
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    # Replace non-alphanumeric with underscore
    name = re.sub(r"[^a-zA-Z0-9_-]", "_", name)
    # Collapse multiple underscores
    name = re.sub(r"_+", "_", name).strip("_")
    return name.lower() or "sensor"


def generate_download_filename(
    sensor_name: str,
    started_at: datetime,
    ended_at: datetime | None = None,
    extension: str = "wav",
) -> str:
    """Generate a standardized, sortable download filename.

    Single:  greenmind_{sensor}_{YYYYMMDD-HHmmss}.wav
    Bundle:  greenmind_{sensor}_{from}_bis_{to}.zip
    """
    import zoneinfo

    switzerland_tz = zoneinfo.ZoneInfo("Europe/Zurich")

    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=UTC)
    local_start = started_at.astimezone(switzerland_tz)

    slug = _sanitize_filename(sensor_name)
    start_str = local_start.strftime("%Y%m%d-%H%M%S")

    if ended_at and extension == "zip":
        if ended_at.tzinfo is None:
            ended_at = ended_at.replace(tzinfo=UTC)
        local_end = ended_at.astimezone(switzerland_tz)
        end_str = local_end.strftime("%Y%m%d-%H%M%S")
        return f"greenmind_{slug}_{start_str}_bis_{end_str}.zip"

    return f"greenmind_{slug}_{start_str}.{extension}"


def stream_wav_zip(s3_keys: list[str], filenames: list[str]) -> Generator[bytes, None, None]:
    """Stream a ZIP archive of multiple WAV files from MinIO.

    Uses a pipe-based approach: a background thread writes entries into
    a zipfile, while the main thread yields chunks from the read-end
    of the pipe.  This keeps RAM usage at ~1 file at a time regardless
    of total bundle size.
    """
    import queue
    import threading
    import zipfile

    client = _get_s3_client()
    chunk_queue: queue.Queue[bytes | None] = queue.Queue(maxsize=32)

    class QueueWriter:
        """File-like object that pushes writes into a queue."""

        def write(self, data: bytes) -> int:
            if data:
                chunk_queue.put(data)
            return len(data)

        def flush(self) -> None:
            pass

    def _build_zip() -> None:
        """Run in background thread: fetch files from S3 and write to ZIP."""
        try:
            writer = QueueWriter()
            with zipfile.ZipFile(writer, "w", zipfile.ZIP_STORED) as zf:
                for s3_key, filename in zip(s3_keys, filenames, strict=True):
                    try:
                        response = client.get_object(Bucket=_WAV_BUCKET, Key=s3_key)
                        data = response["Body"].read()
                        zf.writestr(filename, data)
                    except Exception as exc:
                        logger.warning("Failed to add %s to ZIP: %s", s3_key, exc)
        except Exception as exc:
            logger.error("ZIP build error: %s", exc)
        finally:
            chunk_queue.put(None)  # Sentinel: done

    thread = threading.Thread(target=_build_zip, daemon=True)
    thread.start()

    while True:
        chunk = chunk_queue.get()
        if chunk is None:
            break
        yield chunk

    thread.join(timeout=5.0)


def export_wav_from_session(session_id: str, raw_path: str, sample_rate: int) -> None:
    """Read a raw JSONL session log, convert the signal to a clean WAV, and upload it."""
    import io
    import json
    import os
    import struct

    from app.database import SessionLocal
    from app.models.biosignal import BioSession

    if not os.path.exists(raw_path):
        logger.error(f"Cannot export session {session_id}, raw file not found at {raw_path}")
        return

    frames = []

    # Process the raw signal
    with open(raw_path) as f:
        for line in f:
            if not line.strip():
                continue
            try:
                batch = json.loads(line)
                readings = batch["readings"]
                for r in readings:
                    mv_val = r[0]
                    flags = r[3]

                    # Clean generation: if invalid (flag > 0), set to 0 PCM (1.65V baseline equivalent)
                    if flags != 0:
                        pcm_val = 0
                    else:
                        # AD8232 limits: 0 .. 3300mV. Centered at 1650mV.
                        # Scale perfectly to 16 bit PCM (-32768 to +32767)
                        normalized = (mv_val - 1650.0) / 1650.0
                        # Clamp
                        normalized = max(min(normalized, 1.0), -1.0)
                        pcm_val = int(normalized * 32767)

                    frames.append(struct.pack("<h", pcm_val))
            except Exception as e:
                logger.error(f"Export parsing error in {raw_path}: {e}")

    # Generate WAV in-memory
    wav_io = io.BytesIO()
    with wave.open(wav_io, "wb") as wf:
        wf.setnchannels(1)  # Mono
        wf.setsampwidth(2)  # 16-bit PCM
        wf.setframerate(sample_rate)
        wf.writeframes(b"".join(frames))

    wav_size = wav_io.tell()
    wav_io.seek(0)

    # Store back to DB
    with SessionLocal() as db:
        session = db.query(BioSession).filter(BioSession.id == session_id).first()
        if session:
            # We use upload_wav logic to upload to MinIO
            s3_key = upload_wav(
                wav_io,
                sensor_mac=session.sensor_mac,
                started_at=session.start_time,
                file_size=wav_size,
            )
            session.wav_storage_key = s3_key
            db.commit()
            logger.info(f"Session {session_id} WAV exported successfully to {s3_key}")
