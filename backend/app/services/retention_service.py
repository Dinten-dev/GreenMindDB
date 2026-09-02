"""Coordinated database and object-storage retention."""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, or_, text
from sqlalchemy.orm import Session

from app.config import settings
from app.models.gateway_remote import GatewayStateReport
from app.models.ingest_log import IngestLog
from app.models.operations import RetentionRun, RetentionRunItem
from app.models.pairing import PairingCode
from app.models.provisioning import ProvisioningJob
from app.models.wav_file import WavFeature, WavFile
from app.services import wav_service
from app.services.wav_feature_service import EXTRACTOR_VERSION, PARAMETER_HASH

logger = logging.getLogger(__name__)


def _cutoff(days: int, now: datetime) -> datetime:
    return now - timedelta(days=days)


def _metadata_statements(now: datetime):
    return {
        "ingest_logs": (
            IngestLog,
            IngestLog.created_at < _cutoff(settings.retention_ingest_log_days, now),
        ),
        "gateway_state_reports": (
            GatewayStateReport,
            GatewayStateReport.reported_at < _cutoff(settings.retention_gateway_state_days, now),
        ),
        "pairing_codes": (
            PairingCode,
            PairingCode.expires_at < _cutoff(settings.retention_pairing_days, now),
        ),
        "provisioning_jobs": (
            ProvisioningJob,
            ProvisioningJob.updated_at < _cutoff(settings.retention_provisioning_days, now),
        ),
    }


def _delete_expired_metadata(db: Session, now: datetime) -> dict[str, int]:
    deleted = {}
    for name, (model, condition) in _metadata_statements(now).items():
        primary_key = model.__mapper__.primary_key[0]
        count = 0
        for _ in range(settings.retention_max_batches_per_run):
            ids = [
                row[0]
                for row in db.query(primary_key)
                .filter(condition)
                .order_by(primary_key)
                .limit(settings.retention_batch_size)
                .all()
            ]
            if not ids:
                break
            result = db.execute(delete(model).where(primary_key.in_(ids)))
            count += result.rowcount or 0
            db.commit()
        deleted[name] = count
    return deleted


def _eligible_raw_query(db: Session, now: datetime):
    query = (
        db.query(WavFile)
        .join(WavFeature, WavFeature.wav_file_id == WavFile.id)
        .filter(
            WavFile.started_at < _cutoff(settings.retention_wav_days, now),
            WavFile.raw_deleted_at.is_(None),
            WavFile.feature_status == "verified",
            WavFile.feature_verified_at.isnot(None),
            WavFeature.verified_at.isnot(None),
            WavFeature.feature_checksum.isnot(None),
            WavFeature.extractor_version == EXTRACTOR_VERSION,
            WavFeature.parameter_hash == PARAMETER_HASH,
            WavFeature.calibration_version == WavFile.calibration_version,
        )
    )
    if settings.wav_flac_archive_enabled:
        query = query.filter(
            WavFeature.flac_s3_key.isnot(None),
            WavFeature.flac_verified_at.isnot(None),
        )
    if settings.wav_anomaly_archive_enabled:
        query = query.filter(
            or_(
                WavFeature.is_anomaly.is_(False),
                (
                    WavFeature.anomaly_s3_key.isnot(None)
                    & WavFeature.anomaly_verified_at.isnot(None)
                ),
            )
        )
    return query.order_by(WavFile.started_at)


def _delete_expired_wavs(db: Session, now: datetime) -> int:
    """Delete only raw objects with fully verified configured derivatives."""
    deleted = 0
    for _ in range(settings.retention_max_batches_per_run):
        records = _eligible_raw_query(db, now).limit(settings.retention_batch_size).all()
        if not records:
            break
        for record in records:
            wav_service.delete_wav(record.s3_key)
            record.raw_deleted_at = now
        db.commit()
        deleted += len(records)
    return deleted


def _delete_expired_archives(db: Session, now: datetime) -> dict[str, int]:
    deleted = {"anomaly_archives": 0, "flac_archives": 0}
    anomaly_records = (
        db.query(WavFeature)
        .filter(
            WavFeature.anomaly_s3_key.isnot(None),
            WavFeature.anomaly_expires_at < now,
        )
        .limit(settings.retention_batch_size)
        .all()
    )
    for feature in anomaly_records:
        wav_service.delete_artifact(feature.anomaly_s3_key)
        feature.anomaly_s3_key = None
        feature.anomaly_sha256 = None
        feature.anomaly_file_size_bytes = None
        deleted["anomaly_archives"] += 1

    flac_records = (
        db.query(WavFeature)
        .filter(
            WavFeature.flac_s3_key.isnot(None),
            WavFeature.flac_expires_at < now,
        )
        .limit(settings.retention_batch_size)
        .all()
    )
    for feature in flac_records:
        wav_service.delete_artifact(feature.flac_s3_key)
        feature.flac_s3_key = None
        feature.flac_sha256 = None
        feature.flac_file_size_bytes = None
        deleted["flac_archives"] += 1
    db.commit()
    return deleted


def _delete_expired_features(db: Session, now: datetime) -> int:
    """Delete verified features only after their two-year retention."""
    cutoff = _cutoff(settings.retention_wav_feature_days, now)
    deleted = 0
    for _ in range(settings.retention_max_batches_per_run):
        records = (
            db.query(WavFile)
            .join(WavFeature, WavFeature.wav_file_id == WavFile.id)
            .filter(WavFeature.verified_at < cutoff)
            .order_by(WavFeature.verified_at)
            .limit(settings.retention_batch_size)
            .all()
        )
        if not records:
            break
        for record in records:
            if record.raw_deleted_at is None:
                wav_service.delete_wav(record.s3_key)
            if record.feature.anomaly_s3_key:
                wav_service.delete_artifact(record.feature.anomaly_s3_key)
            if record.feature.flac_s3_key:
                wav_service.delete_artifact(record.feature.flac_s3_key)
            db.delete(record)
        db.commit()
        deleted += len(records)
    return deleted


def retention_dry_run(db: Session, now: datetime | None = None) -> dict[str, int]:
    """Report deletion candidates without changing database or object storage."""
    now = now or datetime.now(UTC)
    ready_count, ready_bytes = (
        _eligible_raw_query(db, now)
        .with_entities(func.count(WavFile.id), func.coalesce(func.sum(WavFile.file_size_bytes), 0))
        .one()
    )
    expired_raw = (
        db.query(WavFile)
        .filter(
            WavFile.started_at < _cutoff(settings.retention_wav_days, now),
            WavFile.raw_deleted_at.is_(None),
        )
        .count()
    )
    result = {
        "dry_run": 1,
        "wav_files": int(ready_count),
        "wav_bytes": int(ready_bytes),
        "wav_blocked_unverified": max(0, expired_raw - int(ready_count)),
        "wav_features": db.query(WavFeature)
        .filter(WavFeature.verified_at < _cutoff(settings.retention_wav_feature_days, now))
        .count(),
        "anomaly_archives": db.query(WavFeature)
        .filter(
            WavFeature.anomaly_s3_key.isnot(None),
            WavFeature.anomaly_expires_at < now,
        )
        .count(),
        "flac_archives": db.query(WavFeature)
        .filter(WavFeature.flac_s3_key.isnot(None), WavFeature.flac_expires_at < now)
        .count(),
    }
    for name, (model, condition) in _metadata_statements(now).items():
        result[name] = db.query(func.count()).select_from(model).filter(condition).scalar() or 0
    return result


def run_retention(db: Session) -> dict[str, int]:
    """Apply one retention cycle after an explicit operator enable."""
    if not settings.retention_enabled:
        raise RuntimeError("Retention is disabled")

    lock_connection = None
    lock_acquired = True
    if db.get_bind().dialect.name == "postgresql":
        lock_connection = db.get_bind().connect()
        lock_acquired = bool(
            lock_connection.execute(
                text("SELECT pg_try_advisory_lock(:lock_id)"),
                {"lock_id": settings.retention_advisory_lock_id},
            ).scalar()
        )
    if not lock_acquired:
        lock_connection.close()
        return {"dry_run": int(settings.retention_dry_run), "lock_skipped": 1}

    now = datetime.now(UTC)
    run = RetentionRun(status="running", dry_run=int(settings.retention_dry_run), result={})
    db.add(run)
    db.commit()
    try:
        if settings.retention_dry_run:
            result = retention_dry_run(db, now)
        else:
            result = {"dry_run": 0, "wav_files": _delete_expired_wavs(db, now)}
            result.update(_delete_expired_archives(db, now))
            result["wav_features"] = _delete_expired_features(db, now)
            dropped_chunks = (
                db.execute(
                    text(
                        "SELECT drop_chunks("
                        "'sensor_reading', older_than => CAST(:cutoff AS timestamptz)"
                        ")"
                    ),
                    {"cutoff": _cutoff(settings.retention_sensor_reading_days, now)},
                )
                .scalars()
                .all()
            )
            result["sensor_reading_chunks"] = len(dropped_chunks)
            result.update(_delete_expired_metadata(db, now))
        for category, count in result.items():
            if category in {"dry_run", "wav_bytes"}:
                continue
            db.add(
                RetentionRunItem(
                    run_id=run.id,
                    category=category,
                    affected_count=int(count),
                    affected_bytes=int(result.get("wav_bytes", 0))
                    if category == "wav_files"
                    else 0,
                )
            )
        run.status = "completed"
        run.finished_at = datetime.now(UTC)
        run.result = result
        db.commit()
    except Exception as exc:
        db.rollback()
        failed = db.query(RetentionRun).filter(RetentionRun.id == run.id).first()
        if failed is not None:
            failed.status = "failed"
            failed.finished_at = datetime.now(UTC)
            failed.error = f"{type(exc).__name__}: {exc}"[:4_000]
            db.commit()
        raise
    finally:
        if lock_connection is not None:
            lock_connection.execute(
                text("SELECT pg_advisory_unlock(:lock_id)"),
                {"lock_id": settings.retention_advisory_lock_id},
            )
            lock_connection.close()

    logger.info("Retention completed: %s", result)
    return result
