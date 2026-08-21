"""Authentication API endpoints: signup, login, logout, me."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.auth import (
    create_access_token,
    delete_auth_cookie,
    get_current_user,
    get_password_hash,
    set_auth_cookie,
    verify_password_and_update,
)
from app.database import get_db
from app.models.user import EmailVerification, Role, User
from app.rate_limit import limiter
from app.schemas.auth import (
    AuthResponse,
    LoginRequest,
    ResendVerificationRequest,
    SignupRequest,
    UserResponse,
    UserUpdateRequest,
    VerifyEmailRequest,
)
from app.services.email_service import EmailService

router = APIRouter(prefix="/auth", tags=["auth"])


def _verification_token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ── Endpoints ────────────────────────────────────


@router.post("/signup", response_model=AuthResponse, status_code=201)
@limiter.limit("5/minute")
async def signup(request: Request, data: SignupRequest, db: Session = Depends(get_db)):
    """Create a new user account pending email verification."""
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
        is_verified=False,
    )
    db.add(user)
    db.flush()  # get user.id

    # Create email verification token
    token_str = secrets.token_hex(16)
    verification = EmailVerification(
        user_id=user.id,
        token=_verification_token_digest(token_str),
        expires_at=datetime.now(UTC) + timedelta(hours=24),
    )
    db.add(verification)
    db.commit()
    db.refresh(user)

    # Dispatch Verification Email
    EmailService.send_verification_email(to_email=user.email, token=token_str)

    return AuthResponse(
        detail="Account created. Verify your email before signing in.",
        user=_user_response(user),
    )


@router.post("/verify-email", status_code=200)
@limiter.limit("3/minute")
async def verify_email(request: Request, data: VerifyEmailRequest, db: Session = Depends(get_db)):
    """Verify a user's email using the token."""
    verification = (
        db.query(EmailVerification)
        .filter(
            EmailVerification.token.in_((_verification_token_digest(data.token), data.token)),
            EmailVerification.used_at.is_(None),
        )
        .first()
    )

    expires_at = verification.expires_at if verification else None
    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if not verification or not expires_at or expires_at < datetime.now(UTC):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired verification token"
        )

    verification.used_at = datetime.now(UTC)
    user = db.query(User).filter(User.id == verification.user_id).first()
    if user:
        user.is_verified = True

    db.commit()
    return {"detail": "Email successfully verified"}


@router.post("/resend-verification", status_code=200)
@limiter.limit("3/hour")
async def resend_verification(
    request: Request,
    data: ResendVerificationRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Issue a fresh verification token without revealing account existence."""
    generic_detail = (
        "If an unverified account exists for that email, a verification message has been sent."
    )
    user = db.query(User).filter(User.email == data.email.lower()).first()
    if not user or not user.is_active or user.is_verified:
        return {"detail": generic_detail}

    now = datetime.now(UTC)
    db.query(EmailVerification).filter(
        EmailVerification.user_id == user.id,
        EmailVerification.used_at.is_(None),
    ).update({EmailVerification.used_at: now}, synchronize_session=False)
    token_str = secrets.token_hex(16)
    db.add(
        EmailVerification(
            user_id=user.id,
            token=_verification_token_digest(token_str),
            expires_at=now + timedelta(hours=24),
        )
    )
    db.commit()

    background_tasks.add_task(
        EmailService.send_verification_email,
        to_email=user.email,
        token=token_str,
    )
    return {"detail": generic_detail}


@router.post("/login", response_model=AuthResponse)
@limiter.limit("5/minute")
async def login(
    request: Request, data: LoginRequest, response: Response, db: Session = Depends(get_db)
):
    """Login and get access token (also set as httpOnly cookie)."""
    user = db.query(User).filter(User.email == data.email.lower()).first()

    # Use dummy verify if user doesn't exist to prevent timing attacks (user enumeration)
    from app.auth import pwd_context

    if not user:
        pwd_context.dummy_verify()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
        )

    password_valid, replacement_hash = verify_password_and_update(data.password, user.password_hash)
    if not password_valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
        )

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled")
    if not user.is_verified:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Email not verified")

    if replacement_hash:
        user.password_hash = replacement_hash
        db.commit()

    token = create_access_token(data={"sub": str(user.id)})
    set_auth_cookie(response, token)

    return AuthResponse(user=_user_response(user))


@router.post("/logout")
async def logout(response: Response):
    """Clear authentication cookie."""
    delete_auth_cookie(response)
    return {"detail": "Logged out"}


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    """Get current authenticated user."""
    return _user_response(current_user)


@router.patch("/me", response_model=UserResponse)
async def update_me(
    data: UserUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update current user profile (name, phone_number)."""
    if data.name is not None:
        current_user.name = data.name
    if data.phone_number is not None:
        current_user.phone_number = data.phone_number

    db.commit()
    db.refresh(current_user)
    return _user_response(current_user)


def _user_response(user: User) -> UserResponse:
    return UserResponse(
        id=str(user.id),
        email=user.email,
        name=user.name,
        phone_number=user.phone_number,
        role=user.role.value if isinstance(user.role, Role) else user.role,
        organization_id=str(user.organization_id) if user.organization_id else None,
        organization_name=user.organization.name if user.organization else None,
        is_active=user.is_active,
    )
