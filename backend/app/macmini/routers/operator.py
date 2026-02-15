"""Operator/Betriebsleiter dashboard endpoints: overview, signals, events, ground-truth, annotations."""
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.macmini.auth import get_current_user, require_operator_or_admin, resolve_greenhouse_id
from app.macmini.database import get_db
from app.macmini.schemas import (
    AnnotationCreate,
    AnnotationOut,
    EventOut,
    GroundTruthCreate,
    GroundTruthOut,
    PlantOut,
)
from app.models import (
    Annotation,
    AnnotationStatus,
    EventLog,
    GroundTruthDaily,
    Plant,
    Sensor,
    User,
    Zone,
)

router = APIRouter(prefix="/operator", tags=["operator"])


# ── Overview KPIs ─────────────────────────────────────────────

@router.get("/overview")
def overview(
    db: Session = Depends(get_db),
    user: User = Depends(require_operator_or_admin),
):
    gh_id = resolve_greenhouse_id(user)
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Count events today
    events_today = (
        db.query(func.count(EventLog.id))
        .filter(EventLog.greenhouse_id == gh_id, EventLog.time >= today_start)
        .scalar()
    ) if gh_id else 0

    # Count plants in greenhouse
    plant_count = 0
    if gh_id:
        plant_count = (
            db.query(func.count(Plant.id))
            .join(Zone, Plant.zone_id == Zone.id)
            .filter(Zone.greenhouse_id == gh_id)
            .scalar()
        )

    # Last signal timestamp
    last_signal = None
    if gh_id:
        row = db.execute(
            text("SELECT MAX(time) FROM plant_signal_1hz WHERE greenhouse_id = :gid"),
            {"gid": str(gh_id)},
        ).scalar()
        if row:
            last_signal = row.isoformat() if hasattr(row, "isoformat") else str(row)

    # Last env measurement
    last_env = None
    if gh_id:
        row = db.execute(
            text("SELECT MAX(time) FROM env_measurement WHERE greenhouse_id = :gid"),
            {"gid": str(gh_id)},
        ).scalar()
        if row:
            last_env = row.isoformat() if hasattr(row, "isoformat") else str(row)

    return {
        "greenhouse_id": str(gh_id) if gh_id else None,
        "events_today": events_today,
        "plant_count": plant_count,
        "last_signal_time": last_signal,
        "last_env_time": last_env,
    }


# ── Plants ────────────────────────────────────────────────────

@router.get("/plants", response_model=list[PlantOut])
def list_plants(
    db: Session = Depends(get_db),
    user: User = Depends(require_operator_or_admin),
):
    gh_id = resolve_greenhouse_id(user)
    q = db.query(Plant).join(Zone, Plant.zone_id == Zone.id)
    if gh_id:
        q = q.filter(Zone.greenhouse_id == gh_id)
    return q.all()


# ── Plant Signal Query ────────────────────────────────────────

_SIGNAL_AGG_VIEW = {"1m": "plant_signal_1hz_1m", "15m": "plant_signal_1hz_15m"}


@router.get("/plants/{plant_id}/signal")
def query_plant_signal(
    plant_id: UUID,
    from_ts: datetime | None = Query(default=None, alias="from"),
    to_ts: datetime | None = Query(default=None, alias="to"),
    agg: Literal["raw", "1m", "15m"] = Query(default="raw"),
    db: Session = Depends(get_db),
    user: User = Depends(require_operator_or_admin),
):
    gh_id = resolve_greenhouse_id(user)
    if not from_ts:
        from_ts = datetime.now(timezone.utc) - timedelta(hours=24)
    if not to_ts:
        to_ts = datetime.now(timezone.utc)

    if agg == "raw":
        sql = text(
            "SELECT time, plant_id, sensor_id, value_uv AS value_uV, quality, meta "
            "FROM plant_signal_1hz "
            "WHERE plant_id = :pid AND time >= :f AND time < :t "
            + ("AND greenhouse_id = :gid " if gh_id else "")
            + "ORDER BY time"
        )
        params: dict = {"pid": str(plant_id), "f": from_ts, "t": to_ts}
        if gh_id:
            params["gid"] = str(gh_id)
    else:
        view = _SIGNAL_AGG_VIEW[agg]
        sql = text(
            f"SELECT bucket, plant_id, sensor_id, value_avg, value_min, value_max, sample_count "
            f"FROM {view} "
            f"WHERE plant_id = :pid AND bucket >= :f AND bucket < :t "
            + ("AND greenhouse_id = :gid " if gh_id else "")
            + "ORDER BY bucket"
        )
        params = {"pid": str(plant_id), "f": from_ts, "t": to_ts}
        if gh_id:
            params["gid"] = str(gh_id)

    rows = db.execute(sql, params).mappings().all()
    return {"points": [dict(r) for r in rows]}


# ── Sensor Env Query ──────────────────────────────────────────

_ENV_AGG_VIEW = {"1m": "env_measurement_1m", "15m": "env_measurement_15m"}


@router.get("/sensors/{sensor_id}/env")
def query_sensor_env(
    sensor_id: UUID,
    from_ts: datetime | None = Query(default=None, alias="from"),
    to_ts: datetime | None = Query(default=None, alias="to"),
    agg: Literal["raw", "1m", "15m"] = Query(default="raw"),
    db: Session = Depends(get_db),
    user: User = Depends(require_operator_or_admin),
):
    gh_id = resolve_greenhouse_id(user)
    if not from_ts:
        from_ts = datetime.now(timezone.utc) - timedelta(hours=24)
    if not to_ts:
        to_ts = datetime.now(timezone.utc)

    if agg == "raw":
        sql = text(
            "SELECT time, sensor_id, value, quality, meta "
            "FROM env_measurement "
            "WHERE sensor_id = :sid AND time >= :f AND time < :t "
            + ("AND greenhouse_id = :gid " if gh_id else "")
            + "ORDER BY time"
        )
        params: dict = {"sid": str(sensor_id), "f": from_ts, "t": to_ts}
        if gh_id:
            params["gid"] = str(gh_id)
    else:
        view = _ENV_AGG_VIEW[agg]
        sql = text(
            f"SELECT bucket, sensor_id, value_avg, value_min, value_max, sample_count "
            f"FROM {view} "
            f"WHERE sensor_id = :sid AND bucket >= :f AND bucket < :t "
            + ("AND greenhouse_id = :gid " if gh_id else "")
            + "ORDER BY bucket"
        )
        params = {"sid": str(sensor_id), "f": from_ts, "t": to_ts}
        if gh_id:
            params["gid"] = str(gh_id)

    rows = db.execute(sql, params).mappings().all()
    return {"points": [dict(r) for r in rows]}


# ── Events ────────────────────────────────────────────────────

@router.get("/events", response_model=list[EventOut])
def list_events(
    from_ts: datetime | None = Query(default=None, alias="from"),
    to_ts: datetime | None = Query(default=None, alias="to"),
    type: str | None = Query(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(require_operator_or_admin),
):
    gh_id = resolve_greenhouse_id(user)
    q = db.query(EventLog)
    if gh_id:
        q = q.filter(EventLog.greenhouse_id == gh_id)
    if from_ts:
        q = q.filter(EventLog.time >= from_ts)
    if to_ts:
        q = q.filter(EventLog.time < to_ts)
    if type:
        q = q.filter(EventLog.type == type)
    return q.order_by(EventLog.time.desc()).limit(200).all()


@router.post("/events", response_model=EventOut, status_code=201)
def create_event(
    payload: dict,
    db: Session = Depends(get_db),
    user: User = Depends(require_operator_or_admin),
):
    from uuid import uuid4

    gh_id = resolve_greenhouse_id(user)
    evt = EventLog(
        greenhouse_id=gh_id,
        time=datetime.fromisoformat(payload.get("time", datetime.now(timezone.utc).isoformat())),
        type=payload["type"],
        payload=payload.get("payload", {}),
        created_by_user_id=user.id,
        request_id=uuid4(),
    )
    db.add(evt)
    db.commit()
    db.refresh(evt)
    return evt


# ── Ground Truth ──────────────────────────────────────────────

@router.get("/ground-truth", response_model=list[GroundTruthOut])
def list_ground_truth(
    plant_id: UUID | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_operator_or_admin),
):
    gh_id = resolve_greenhouse_id(user)
    q = db.query(GroundTruthDaily)
    if gh_id:
        q = q.filter(GroundTruthDaily.greenhouse_id == gh_id)
    if plant_id:
        q = q.filter(GroundTruthDaily.plant_id == plant_id)
    return q.order_by(GroundTruthDaily.date.desc()).limit(100).all()


@router.post("/ground-truth/daily", response_model=GroundTruthOut, status_code=201)
def create_ground_truth(
    payload: GroundTruthCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_operator_or_admin),
):
    gh_id = resolve_greenhouse_id(user)
    gt = GroundTruthDaily(
        greenhouse_id=gh_id,
        plant_id=payload.plant_id,
        date=payload.date,
        vitality_score=payload.vitality_score,
        growth_score=payload.growth_score,
        pest_score=payload.pest_score,
        disease_score=payload.disease_score,
        notes=payload.notes,
        created_by_user_id=user.id,
    )
    db.add(gt)
    db.commit()
    db.refresh(gt)
    return gt


# ── Annotations ───────────────────────────────────────────────

@router.get("/annotations", response_model=list[AnnotationOut])
def list_annotations(
    status_filter: str | None = Query(default=None, alias="status"),
    db: Session = Depends(get_db),
    user: User = Depends(require_operator_or_admin),
):
    gh_id = resolve_greenhouse_id(user)
    q = db.query(Annotation)
    if gh_id:
        q = q.filter(Annotation.greenhouse_id == gh_id)
    if status_filter:
        q = q.filter(Annotation.status == AnnotationStatus(status_filter))
    return q.order_by(Annotation.created_at.desc()).all()


@router.post("/annotations", response_model=AnnotationOut, status_code=201)
def create_annotation(
    payload: AnnotationCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_operator_or_admin),
):
    gh_id = resolve_greenhouse_id(user)
    ann = Annotation(
        greenhouse_id=gh_id,
        plant_id=payload.plant_id,
        sensor_id=payload.sensor_id,
        start_time=payload.start_time,
        end_time=payload.end_time,
        label_key=payload.label_key,
        label_value=payload.label_value,
        confidence=payload.confidence,
        notes=payload.notes,
        created_by_user_id=user.id,
        status=AnnotationStatus.DRAFT,
    )
    db.add(ann)
    db.commit()
    db.refresh(ann)
    return ann


@router.post("/annotations/{annotation_id}/submit", response_model=AnnotationOut)
def submit_annotation(
    annotation_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_operator_or_admin),
):
    gh_id = resolve_greenhouse_id(user)
    ann = db.query(Annotation).filter(Annotation.id == annotation_id).first()
    if not ann:
        raise HTTPException(status_code=404, detail="Annotation not found")
    if gh_id and ann.greenhouse_id != gh_id:
        raise HTTPException(status_code=403, detail="Cannot access annotation from other greenhouse")
    if ann.status != AnnotationStatus.DRAFT:
        raise HTTPException(status_code=400, detail="Only draft annotations can be submitted")
    ann.status = AnnotationStatus.SUBMITTED
    db.commit()
    db.refresh(ann)
    return ann
