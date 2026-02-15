"""Auth endpoints: login, refresh, logout."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.macmini.auth import (
    create_access_token,
    create_refresh_token,
    get_current_user,
    hash_password,
    revoke_refresh_token,
    rotate_refresh_token,
    verify_password,
)
from app.macmini.database import get_db
from app.macmini.schemas import (
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    TokenResponse,
)
from app.models import AuditLog, User

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled")

    access = create_access_token(user)
    refresh = create_refresh_token(db, user)

    user.last_login = datetime.now(timezone.utc)
    db.commit()

    # Audit
    db.add(AuditLog(
        actor_user_id=user.id,
        actor_type="USER",
        action="login",
        resource_type="user",
        resource_id=str(user.id),
        greenhouse_id=user.greenhouse_id,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    ))
    db.commit()

    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        role=user.role.value,
        greenhouse_id=user.greenhouse_id,
    )


@router.post("/refresh", response_model=TokenResponse)
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)):
    user, new_refresh = rotate_refresh_token(db, payload.refresh_token)
    access = create_access_token(user)
    return TokenResponse(
        access_token=access,
        refresh_token=new_refresh,
        role=user.role.value,
        greenhouse_id=user.greenhouse_id,
    )


@router.post("/logout", status_code=204)
def logout(payload: LogoutRequest, db: Session = Depends(get_db)):
    revoke_refresh_token(db, payload.refresh_token)
