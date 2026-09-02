"""Shared worker heartbeat helpers."""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.operations import BackgroundWorkerHeartbeat


def update_heartbeat(db: Session, worker_name: str, status: str, details: dict) -> None:
    heartbeat = (
        db.query(BackgroundWorkerHeartbeat)
        .filter(BackgroundWorkerHeartbeat.worker_name == worker_name)
        .first()
    )
    if heartbeat is None:
        heartbeat = BackgroundWorkerHeartbeat(worker_name=worker_name)
        db.add(heartbeat)
    heartbeat.status = status
    heartbeat.details = details
    heartbeat.heartbeat_at = datetime.now(UTC)
    db.commit()
