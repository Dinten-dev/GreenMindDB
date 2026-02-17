"""Admin endpoints: user management, master data CRUD, label schemas, annotation review, audit."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.macmini.auth import hash_password, require_admin
from app.macmini.database import get_db
from app.macmini.schemas import (
    AnnotationOut,
    AnnotationReviewCreate,
    AnnotationReviewOut,
    AuditLogOut,
    DeviceCreate,
    DeviceOut,
    GreenhouseCreate,
    GreenhouseOut,
    LabelSchemaCreate,
    LabelSchemaOut,
    PlantCreate,
    PlantOut,
    SensorCreate,
    SensorOut,
    UserCreate,
    UserOut,
    UserUpdate,
    ZoneCreate,
    ZoneOut,
    GreenhouseSummary,
    DeviceKeyResponse,
)
from app.models import (
    Annotation,
    AnnotationReview,
    AnnotationStatus,
    AuditLog,
    Device,
    Greenhouse,
    LabelSchema,
    Plant,
    ReviewDecision,
    Role,
    Sensor,
    User,
    Zone,
)
import secrets
from datetime import datetime, timezone
from sqlalchemy import func

router = APIRouter(prefix="/admin", tags=["admin"])


# ── Users ─────────────────────────────────────────────────────

@router.get("/users", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return db.query(User).order_by(User.created_at.desc()).all()


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(
    payload: UserCreate,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(status_code=409, detail="Email already registered")
    user = User(
        email=payload.email,
        password_hash=hash_password(payload.password),
        role=Role(payload.role),
        greenhouse_id=payload.greenhouse_id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    db.add(AuditLog(
        actor_user_id=admin.id, actor_type="USER", action="create_user",
        resource_type="user", resource_id=str(user.id),
        greenhouse_id=user.greenhouse_id,
        ip=request.client.host if request.client else None,
    ))
    db.commit()
    return user


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: UUID,
    payload: UserUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if payload.role is not None:
        user.role = Role(payload.role)
    if payload.greenhouse_id is not None:
        user.greenhouse_id = payload.greenhouse_id
    if payload.is_active is not None:
        user.is_active = payload.is_active
    db.commit()
    db.refresh(user)
    return user


# ── Greenhouses ───────────────────────────────────────────────

@router.get("/greenhouses", response_model=list[GreenhouseOut])
def list_greenhouses(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return db.query(Greenhouse).all()


@router.post("/greenhouses", response_model=GreenhouseOut, status_code=201)
def create_greenhouse(payload: GreenhouseCreate, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    gh = Greenhouse(name=payload.name, location=payload.location, timezone=payload.timezone)
    db.add(gh)
    db.commit()
    db.refresh(gh)
    return gh


@router.get("/greenhouses/{greenhouse_id}", response_model=GreenhouseOut)
def get_greenhouse(greenhouse_id: UUID, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    gh = db.query(Greenhouse).filter(Greenhouse.id == greenhouse_id).first()
    if not gh:
        raise HTTPException(status_code=404, detail="Greenhouse not found")
    return gh


@router.get("/greenhouses/{greenhouse_id}/summary", response_model=GreenhouseSummary)
def get_greenhouse_summary(greenhouse_id: UUID, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    gh = db.query(Greenhouse).filter(Greenhouse.id == greenhouse_id).first()
    if not gh:
        raise HTTPException(status_code=404, detail="Greenhouse not found")
    
    device_count = db.query(func.count(Device.id)).filter(Device.greenhouse_id == greenhouse_id).scalar()
    plant_count = db.query(func.count(Plant.id)).join(Zone).filter(Zone.greenhouse_id == greenhouse_id).scalar()
    
    active_device_count = db.query(func.count(Device.id)).filter(
        Device.greenhouse_id == greenhouse_id,
        Device.status == 'online'
    ).scalar()
    
    last_seen = db.query(func.max(Device.last_seen)).filter(Device.greenhouse_id == greenhouse_id).scalar()
    
    return GreenhouseSummary(
        greenhouse_id=gh.id,
        name=gh.name,
        device_count=device_count or 0,
        plant_count=plant_count or 0,
        active_device_count=active_device_count or 0,
        last_seen=last_seen
    )


# ── Zones ─────────────────────────────────────────────────────

@router.get("/zones", response_model=list[ZoneOut])
def list_zones(greenhouse_id: UUID | None = None, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    q = db.query(Zone)
    if greenhouse_id:
        q = q.filter(Zone.greenhouse_id == greenhouse_id)
    return q.all()


@router.post("/zones", response_model=ZoneOut, status_code=201)
def create_zone(payload: ZoneCreate, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    z = Zone(greenhouse_id=payload.greenhouse_id, name=payload.name)
    db.add(z)
    db.commit()
    db.refresh(z)
    return z


# ── Plants ────────────────────────────────────────────────────

@router.get("/plants", response_model=list[PlantOut])
def list_plants(zone_id: UUID | None = None, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    q = db.query(Plant)
    if zone_id:
        q = q.filter(Plant.zone_id == zone_id)
    return q.all()


@router.post("/plants", response_model=PlantOut, status_code=201)
def create_plant(payload: PlantCreate, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    p = Plant(
        zone_id=payload.zone_id,
        species=payload.species,
        cultivar=payload.cultivar,
        planted_at=payload.planted_at,
        tags=payload.tags,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


# ── Devices ───────────────────────────────────────────────────

@router.get("/devices", response_model=list[DeviceOut])
def list_devices(greenhouse_id: UUID | None = None, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    q = db.query(Device)
    if greenhouse_id:
        q = q.filter(Device.greenhouse_id == greenhouse_id)
    return q.all()


@router.post("/devices", response_model=DeviceKeyResponse, status_code=201)
def create_device(payload: DeviceCreate, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    # Generate API Key
    plain_key = f"gmd_{secrets.token_urlsafe(24)}"
    key_hash = hash_password(plain_key)

    d = Device(
        greenhouse_id=payload.greenhouse_id,
        serial=payload.serial,
        type=payload.type,
        fw_version=payload.fw_version,
        api_key_hash=key_hash,
        api_key_last_rotated_at=datetime.now(timezone.utc),
        status="offline",
        is_active=True
    )
    db.add(d)
    db.commit()
    db.refresh(d)

    db.add(AuditLog(
        actor_user_id=admin.id, actor_type="USER", action="create_device",
        resource_type="device", resource_id=str(d.id),
        greenhouse_id=d.greenhouse_id,
        ip=request.client.host if request.client else None,
    ))
    db.commit()

    return DeviceKeyResponse(device_id=d.id, api_key=plain_key)


@router.post("/devices/{device_id}/rotate-key", response_model=DeviceKeyResponse)
def rotate_device_key(device_id: UUID, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
        
    plain_key = f"gmd_{secrets.token_urlsafe(24)}"
    device.api_key_hash = hash_password(plain_key)
    device.api_key_last_rotated_at = datetime.now(timezone.utc)
    
    db.add(AuditLog(
        actor_user_id=admin.id, actor_type="USER", action="rotate_device_key",
        resource_type="device", resource_id=str(device.id),
        greenhouse_id=device.greenhouse_id,
        ip=request.client.host if request.client else None,
    ))
    db.commit()
    
    return DeviceKeyResponse(device_id=device.id, api_key=plain_key)


# ── Sensors ───────────────────────────────────────────────────

@router.get("/sensors", response_model=list[SensorOut])
def list_sensors(device_id: UUID | None = None, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    q = db.query(Sensor)
    if device_id:
        q = q.filter(Sensor.device_id == device_id)
    return q.all()


@router.post("/sensors", response_model=SensorOut, status_code=201)
def create_sensor(payload: SensorCreate, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    s = Sensor(
        device_id=payload.device_id,
        plant_id=payload.plant_id,
        zone_id=payload.zone_id,
        kind=payload.kind,
        unit=payload.unit,
        calibration=payload.calibration,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


# ── Label Schemas ─────────────────────────────────────────────

@router.get("/label-schemas", response_model=list[LabelSchemaOut])
def list_label_schemas(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return db.query(LabelSchema).filter(LabelSchema.is_active.is_(True)).all()


@router.post("/label-schemas", response_model=LabelSchemaOut, status_code=201)
def create_label_schema(payload: LabelSchemaCreate, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    ls = LabelSchema(
        greenhouse_id=payload.greenhouse_id,
        name=payload.name,
        description=payload.description,
        values=payload.values,
        version=payload.version,
    )
    db.add(ls)
    db.commit()
    db.refresh(ls)
    return ls


# ── Annotation Review (admin only) ───────────────────────────

@router.get("/annotations", response_model=list[AnnotationOut])
def list_submitted_annotations(
    status_filter: str = Query(default="submitted", alias="status"),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    q = db.query(Annotation)
    if status_filter:
        q = q.filter(Annotation.status == AnnotationStatus(status_filter))
    return q.order_by(Annotation.created_at.desc()).all()


@router.post("/annotations/{annotation_id}/review", response_model=AnnotationReviewOut, status_code=201)
def review_annotation(
    annotation_id: UUID,
    payload: AnnotationReviewCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    ann = db.query(Annotation).filter(Annotation.id == annotation_id).first()
    if not ann:
        raise HTTPException(status_code=404, detail="Annotation not found")
    if ann.status != AnnotationStatus.SUBMITTED:
        raise HTTPException(status_code=400, detail="Annotation must be in 'submitted' status to review")

    review = AnnotationReview(
        annotation_id=annotation_id,
        reviewed_by_user_id=admin.id,
        decision=ReviewDecision(payload.decision),
        notes=payload.notes,
    )
    db.add(review)
    ann.status = AnnotationStatus.REVIEWED if payload.decision == "approve" else AnnotationStatus.REJECTED
    db.commit()
    db.refresh(review)
    return review


# ── Audit ─────────────────────────────────────────────────────

@router.get("/audit", response_model=list[AuditLogOut])
def list_audit_logs(
    greenhouse_id: UUID | None = None,
    user_id: UUID | None = None,
    action: str | None = None,
    limit: int = Query(default=100, le=500),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    q = db.query(AuditLog)
    if greenhouse_id:
        q = q.filter(AuditLog.greenhouse_id == greenhouse_id)
    if user_id:
        q = q.filter(AuditLog.actor_user_id == user_id)
    if action:
        q = q.filter(AuditLog.action == action)
    return q.order_by(AuditLog.time.desc()).limit(limit).all()
