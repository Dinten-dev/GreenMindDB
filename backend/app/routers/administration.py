"""Allowlisted platform administration; no reception, storage deletion or remote commands."""

import json
import os
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import get_current_user, get_password_hash
from app.config import settings
from app.database import get_db
from app.models.audit_log import AuditLog
from app.models.master import Zone
from app.models.user import Organization, Role, User
from app.models.zone_access import ZoneAccess
from app.schemas.administration import (
    AdminCompanyUpdate,
    AdminUserCreate,
    AdminUserDelete,
    AdminUserUpdate,
)
from app.zone_access import zone_access_filter

router = APIRouter(prefix="/administration", tags=["administration"])


def is_management_admin(user: User) -> bool:
    allowed = {
        email.strip().casefold()
        for email in settings.management_admin_emails.split(",")
        if email.strip()
    }
    return bool(user.is_active and user.is_verified and user.email.casefold() in allowed)


def require_management_admin(user: User = Depends(get_current_user)) -> User:
    if not is_management_admin(user):
        raise HTTPException(403, "Kein Zugang zur Administration.")
    return user


@router.get("/capabilities")
def capabilities(response: Response, user: User = Depends(get_current_user)):
    response.headers["Cache-Control"] = "private, no-store"
    return {"can_manage": is_management_admin(user)}


@router.get("/storage")
def storage(response: Response, user: User = Depends(require_management_admin)):
    response.headers["Cache-Control"] = "private, no-store"
    path = settings.storage_monitor_path
    if not path:
        raise HTTPException(503, "Speicherüberwachung ist noch nicht eingerichtet.")
    try:
        stats = os.statvfs(path)
        total = stats.f_blocks * stats.f_frsize
        available = stats.f_bavail * stats.f_frsize
        if total <= 0 or not 0 <= available <= total:
            raise OSError("Invalid filesystem statistics")
    except OSError:
        raise HTTPException(503, "Speicherstatus momentan nicht verfügbar.") from None
    free_percent = available * 100 / total
    return {
        "total_bytes": total,
        "available_bytes": available,
        "used_bytes": total - available,
        "free_percent": free_percent,
        "used_percent": 100 - free_percent,
        "warning_threshold_percent": settings.storage_warning_free_percent,
        "warning": free_percent < settings.storage_warning_free_percent,
        "checked_at": datetime.now(UTC).isoformat(),
    }


def view(user, grants, db):
    accessible = user.is_active and user.is_verified
    zones = (
        db.query(Zone).filter(zone_access_filter(user)).order_by(Zone.name, Zone.id).all()
        if accessible
        else []
    )
    return {
        "visible_zones": [{"id": str(z.id), "name": z.name} for z in zones],
        "access_note": (
            "Konto deaktiviert: kein Zugang."
            if not user.is_active
            else "E-Mail unbestätigt: kein Zugang."
            if not user.is_verified
            else "Keine Firma zugeordnet: kein Zonenzugang."
            if not user.organization_id
            else "Alle aktuellen und künftigen Zonen dieser Firma."
            if user.role in (Role.OWNER, Role.ADMIN)
            else "Nur ausdrücklich freigegebene Zonen dieser Firma."
        ),
        "id": str(user.id),
        "email": user.email,
        "name": user.name,
        "phone_number": user.phone_number,
        "role": user.role,
        "organization_id": str(user.organization_id) if user.organization_id else None,
        "organization_name": user.organization.name if user.organization else None,
        "is_active": user.is_active,
        "is_verified": user.is_verified,
        "zone_ids": sorted(map(str, grants)),
        "all_zones": user.role in (Role.OWNER, Role.ADMIN),
        "protected": user.email.casefold()
        in {e.strip().casefold() for e in settings.management_admin_emails.split(",")},
    }


@router.get("/catalog")
def catalog(
    response: Response,
    user: User = Depends(require_management_admin),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "private, no-store"
    return {
        "companies": [
            {"id": str(o.id), "name": o.name}
            for o in db.query(Organization).order_by(Organization.name).all()
        ],
        "zones": [
            {"id": str(z.id), "name": z.name, "organization_id": str(z.organization_id)}
            for z in db.query(Zone).order_by(Zone.name).all()
        ],
    }


@router.get("/users")
def users(
    response: Response,
    user: User = Depends(require_management_admin),
    db: Session = Depends(get_db),
    search: str = Query("", max_length=200),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    response.headers["Cache-Control"] = "private, no-store"
    query = db.query(User)
    if search.strip():
        term = search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.filter(
            or_(
                User.email.ilike(f"%{term}%", escape="\\"),
                User.name.ilike(f"%{term}%", escape="\\"),
            )
        )
    total = query.count()
    rows = query.order_by(User.email, User.id).offset(offset).limit(limit).all()
    grants = {}
    for grant in db.query(ZoneAccess).filter(ZoneAccess.user_id.in_([u.id for u in rows])).all():
        grants.setdefault(grant.user_id, []).append(grant.zone_id)
    return {"users": [view(u, grants.get(u.id, []), db) for u in rows], "total": total}


def validate_zones(db, company, zone_ids, role):
    selected = set(zone_ids)
    if role != Role.MEMBER and selected:
        raise HTTPException(422, "Firmenadministratoren und Eigentümer sehen alle Firmenzonen.")
    existing = {
        r[0]
        for r in db.query(Zone.id)
        .filter(Zone.organization_id == company, Zone.id.in_(selected))
        .all()
    }
    if existing != selected:
        raise HTTPException(422, "Alle Zonen müssen zur gewählten Firma gehören.")
    return selected


def audit(db, actor, user, action, before, after):
    db.add(
        AuditLog(
            user_id=actor.id,
            action=action,
            entity_type="user",
            entity_id=str(user.id),
            details=json.dumps({"before": before, "after": after}),
        )
    )


def commit(db):
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            409, "Diese E-Mail-Adresse wird bereits verwendet oder die Zuordnung wurde geändert."
        ) from None


@router.post("/users", status_code=201)
def create_user(
    data: AdminUserCreate,
    actor: User = Depends(require_management_admin),
    db: Session = Depends(get_db),
):
    if not db.query(Organization).filter_by(id=data.organization_id).with_for_update().first():
        raise HTTPException(404, "Firma nicht gefunden.")
    selected = validate_zones(db, data.organization_id, data.zone_ids, data.role)
    email = str(data.email).strip().lower()
    if db.query(User.id).filter(func.lower(User.email) == email).first():
        raise HTTPException(409, "Diese E-Mail-Adresse wird bereits verwendet.")
    # Accounts in the operator allowlist cannot be provisioned through the browser.
    if email in {e.strip().casefold() for e in settings.management_admin_emails.split(",")}:
        raise HTTPException(409, "Administrationskonten werden separat eingerichtet.")
    user = User(
        id=uuid.uuid4(),
        email=email,
        name=data.name or email.split("@")[0],
        password_hash=get_password_hash(data.password),
        organization_id=data.organization_id,
        role=data.role,
        is_active=True,
        is_verified=True,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Diese E-Mail-Adresse wird bereits verwendet.") from None
    db.add_all(ZoneAccess(user_id=user.id, zone_id=z) for z in selected)
    audit(
        db,
        actor,
        user,
        "administration.user.create",
        None,
        {
            "email": email,
            "organization_id": str(data.organization_id),
            "role": data.role,
            "zone_ids": sorted(map(str, selected)),
        },
    )
    commit(db)
    db.refresh(user)
    return view(user, selected, db)


@router.put("/users/{user_id}")
def update_user(
    user_id: uuid.UUID,
    data: AdminUserUpdate,
    actor: User = Depends(require_management_admin),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter_by(id=user_id).with_for_update(of=User).first()
    if not user:
        raise HTTPException(404, "Benutzer nicht gefunden.")
    if user.email.casefold() in {
        e.strip().casefold() for e in settings.management_admin_emails.split(",")
    }:
        raise HTTPException(409, "Administrationskonten sind vor Änderungen geschützt.")
    # Serialize company-manager removal, including concurrent edits of different users.
    companies = (
        db.query(Organization)
        .filter(Organization.id.in_({user.organization_id, data.organization_id} - {None}))
        .order_by(Organization.id)
        .with_for_update()
        .all()
    )
    if data.organization_id not in {o.id for o in companies}:
        raise HTTPException(404, "Firma nicht gefunden.")
    selected = validate_zones(db, data.organization_id, data.zone_ids, data.role)
    loses_management = (
        not data.is_active
        or data.organization_id != user.organization_id
        or data.role == Role.MEMBER
    )
    protect_last_manager(db, user, loses_management)
    old_grants = [r[0] for r in db.query(ZoneAccess.zone_id).filter_by(user_id=user.id).all()]
    before = view(user, old_grants, db)
    user.name = data.name
    user.phone_number = data.phone_number
    user.organization_id = data.organization_id
    user.role = data.role
    user.is_active = data.is_active
    db.query(ZoneAccess).filter_by(user_id=user.id).delete(synchronize_session=False)
    db.add_all(ZoneAccess(user_id=user.id, zone_id=z) for z in selected)
    audit(
        db,
        actor,
        user,
        "administration.user.update",
        before,
        {
            "organization_id": str(data.organization_id),
            "role": data.role,
            "is_active": data.is_active,
            "zone_ids": sorted(map(str, selected)),
            "name": data.name,
            "phone_number": data.phone_number,
        },
    )
    commit(db)
    db.refresh(user)
    return view(user, selected, db)


def protect_last_manager(db, user, loses_management=True):
    if (
        user.is_active
        and user.organization_id
        and user.role in (Role.OWNER, Role.ADMIN)
        and loses_management
    ):
        remaining = (
            db.query(User.id)
            .filter(
                User.organization_id == user.organization_id,
                User.id != user.id,
                User.is_active.is_(True),
                User.is_verified.is_(True),
                User.role.in_([Role.OWNER, Role.ADMIN]),
            )
            .first()
        )
        if not remaining:
            raise HTTPException(
                409, "Die Firma benötigt mindestens einen aktiven Eigentümer oder Administrator."
            )


@router.delete("/users/{user_id}", status_code=204)
def delete_user(
    user_id: uuid.UUID,
    data: AdminUserDelete,
    actor: User = Depends(require_management_admin),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter_by(id=user_id).with_for_update(of=User).first()
    if not user:
        raise HTTPException(404, "Benutzer nicht gefunden.")
    if (
        user.id == actor.id
        or is_management_admin(user)
        or user.email.casefold()
        in {e.strip().casefold() for e in settings.management_admin_emails.split(",")}
    ):
        raise HTTPException(409, "Administrationskonten können nicht gelöscht werden.")
    if str(data.confirmation_email).casefold() != user.email.casefold():
        raise HTTPException(422, "Bitte die E-Mail-Adresse des zu löschenden Kontos bestätigen.")
    if user.organization_id:
        db.query(Organization).filter_by(id=user.organization_id).with_for_update().one()
    protect_last_manager(db, user)
    grants = [r[0] for r in db.query(ZoneAccess.zone_id).filter_by(user_id=user.id).all()]
    audit(db, actor, user, "administration.user.delete", view(user, grants, db), None)
    # PostgreSQL cascades account grants/verification/pairing and keeps recordings,
    # organizations and historical audit/observation rows (SET NULL actor FKs).
    db.delete(user)
    commit(db)
    return Response(status_code=204)


@router.put("/companies/{company_id}")
def update_company(
    company_id: uuid.UUID,
    data: AdminCompanyUpdate,
    actor: User = Depends(require_management_admin),
    db: Session = Depends(get_db),
):
    company = db.query(Organization).filter_by(id=company_id).with_for_update().first()
    if not company:
        raise HTTPException(404, "Firma nicht gefunden.")
    before = company.name
    company.name = data.name
    db.add(
        AuditLog(
            user_id=actor.id,
            action="administration.company.update",
            entity_type="organization",
            entity_id=str(company.id),
            details=json.dumps({"before": {"name": before}, "after": {"name": data.name}}),
        )
    )
    commit(db)
    return {"id": str(company.id), "name": company.name}
