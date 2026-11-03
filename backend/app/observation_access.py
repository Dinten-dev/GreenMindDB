"""Common transaction and locking boundary for public plant capabilities."""

from contextlib import contextmanager

from sqlalchemy import func, or_

from app.models.observation import PlantObservationAccess, PlantObservationSession
from app.models.plant import Plant


@contextmanager
def observation_transaction(db):
    try:
        yield
        db.commit()
    except Exception:
        db.rollback()
        raise


def lock_observation_plant(db, plant_id):
    # key_share=True with read=False emits PostgreSQL FOR NO KEY UPDATE.
    # This serializes capability operations while allowing FK KEY SHARE locks
    # when observations, evaluations or sessions reference the same plant.
    return (
        db.query(Plant)
        .filter(Plant.id == plant_id)
        .populate_existing()
        .with_for_update(key_share=True)
        .first()
    )


def deactivate_plant_access(db, plant_id, now):
    access_ids = db.query(PlantObservationAccess.id).filter(
        PlantObservationAccess.plant_id == plant_id
    )
    db.query(PlantObservationSession).filter(
        or_(
            PlantObservationSession.plant_id == plant_id,
            PlantObservationSession.access_id.in_(access_ids),
        )
    ).update({"is_active": False}, synchronize_session="fetch")
    db.query(PlantObservationAccess).filter(PlantObservationAccess.plant_id == plant_id).update(
        {"is_active": False, "revoked_at": func.coalesce(PlantObservationAccess.revoked_at, now)},
        synchronize_session="fetch",
    )


def has_single_live_access(db, plant_id):
    """Fail closed on ambiguous legacy data even before schema preparation."""
    active = (
        db.query(PlantObservationAccess)
        .filter(PlantObservationAccess.plant_id == plant_id, PlantObservationAccess.is_active)
        .populate_existing()
        .limit(2)
        .all()
    )
    return len(active) == 1 and active[0].revoked_at is None
