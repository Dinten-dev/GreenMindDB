"""Authentication & RBAC utilities: password hashing, JWT, role enforcement."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Optional
from uuid import uuid4

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.macmini.config import get_settings
from app.macmini.database import get_db
from app.models.user import RefreshToken, Role, User

settings = get_settings()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer(auto_error=False)


# ── Password helpers ──────────────────────────────────────────
def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


# ── Token helpers ─────────────────────────────────────────────
def _token_hash(token: str) -> str:
    return sha256(token.encode()).hexdigest()


def create_access_token(user: User) -> str:
    payload = {
        "sub": str(user.id),
        "role": user.role.value,
        "greenhouse_id": str(user.greenhouse_id) if user.greenhouse_id else None,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_access_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm="HS256")


def create_refresh_token(db: Session, user: User) -> str:
    raw_token = uuid4().hex + uuid4().hex  # 64-char random string
    rt = RefreshToken(
        user_id=user.id,
        token_hash=_token_hash(raw_token),
        expires_at=datetime.now(timezone.utc) + timedelta(days=settings.jwt_refresh_expire_days),
    )
    db.add(rt)
    db.commit()
    return raw_token


def rotate_refresh_token(db: Session, raw_token: str) -> tuple[User, str]:
    """Validate existing refresh token, revoke it, and issue new one. Returns (user, new_raw_token)."""
    h = _token_hash(raw_token)
    rt = (
        db.query(RefreshToken)
        .filter(RefreshToken.token_hash == h, RefreshToken.revoked_at.is_(None))
        .first()
    )
    if not rt or rt.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired refresh token")

    user = db.query(User).filter(User.id == rt.user_id, User.is_active.is_(True)).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or disabled")

    # Revoke old
    rt.revoked_at = datetime.now(timezone.utc)
    db.commit()

    # Issue new
    new_raw = create_refresh_token(db, user)
    return user, new_raw


def revoke_refresh_token(db: Session, raw_token: str) -> None:
    h = _token_hash(raw_token)
    rt = db.query(RefreshToken).filter(RefreshToken.token_hash == h, RefreshToken.revoked_at.is_(None)).first()
    if rt:
        rt.revoked_at = datetime.now(timezone.utc)
        db.commit()


# ── Dependency: current user from JWT ─────────────────────────
def _decode_access_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, settings.jwt_secret_key, algorithms=["HS256"])
    except JWTError:
        return None


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    payload = _decode_access_token(credentials.credentials)
    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    user = db.query(User).filter(User.id == payload["sub"]).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or disabled")
    return user


# ── Role-based dependencies ──────────────────────────────────
async def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != Role.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user


async def require_operator_or_admin(user: User = Depends(get_current_user)) -> User:
    if user.role not in (Role.ADMIN, Role.OPERATOR):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Operator or admin access required")
    return user


# ── Ingest auth (gateway bearer token, separate from user JWT) ─
def require_ingest_auth(authorization: Optional[str] = Header(default=None)) -> None:
    if not authorization:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing ingest token")
    prefix = "Bearer "
    token = authorization[len(prefix):].strip() if authorization.startswith(prefix) else authorization
    if token != settings.ingest_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid ingest token")


# ── Greenhouse scoping helper ────────────────────────────────
def resolve_greenhouse_id(user: User, requested_id=None):
    """Return the greenhouse_id to scope queries.
    ADMIN can query any greenhouse_id.
    OPERATOR is locked to their assigned greenhouse_id.
    """
    if user.role == Role.ADMIN:
        return requested_id  # admin can filter by any or see all
    # operator must be scoped
    if requested_id and str(requested_id) != str(user.greenhouse_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot access other greenhouse data")
    return user.greenhouse_id
