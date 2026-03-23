"""Authentication API endpoints: signup, login, logout, me."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.auth import (
    create_access_token,
    delete_auth_cookie,
    get_current_user,
    get_password_hash,
    set_auth_cookie,
    verify_password,
)
from app.database import get_db
from app.models.user import Role, User
from app.schemas.auth import LoginRequest, SignupRequest, TokenResponse, UserResponse

router = APIRouter(prefix="/auth", tags=["auth"])


# ── Endpoints ────────────────────────────────────


@router.post("/signup", response_model=TokenResponse, status_code=201)
async def signup(
    request: Request, data: SignupRequest, response: Response, db: Session = Depends(get_db)
):
    """Create a new user account and auto-login."""
    existing = db.query(User).filter(User.email == data.email.lower()).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Could not create account"
        )

    user = User(
        email=data.email.lower(),
        name=data.name or data.email.split("@")[0],
        password_hash=get_password_hash(data.password),
        role=Role.OWNER,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token(data={"sub": str(user.id)})
    set_auth_cookie(response, token)

    return TokenResponse(
        access_token=token,
        user=_user_response(user),
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    request: Request, data: LoginRequest, response: Response, db: Session = Depends(get_db)
):
    """Login and get access token (also set as httpOnly cookie)."""
    user = db.query(User).filter(User.email == data.email.lower()).first()
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
        )
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled")

    token = create_access_token(data={"sub": str(user.id)})
    set_auth_cookie(response, token)

    return TokenResponse(
        access_token=token,
        user=_user_response(user),
    )


@router.post("/logout")
async def logout(response: Response):
    """Clear authentication cookie."""
    delete_auth_cookie(response)
    return {"detail": "Logged out"}


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    """Get current authenticated user."""
    return _user_response(current_user)


def _user_response(user: User) -> UserResponse:
    return UserResponse(
        id=str(user.id),
        email=user.email,
        name=user.name,
        role=user.role.value if isinstance(user.role, Role) else user.role,
        organization_id=str(user.organization_id) if user.organization_id else None,
        organization_name=user.organization.name if user.organization else None,
        is_active=user.is_active,
    )
