"""Atomic, email-bound invitation redemption and auditable provider dispatch."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import get_password_hash
from app.models.access import AccessInvitation
from app.models.form_submission import FormSubmission
from app.models.user import EmailVerification, Role, User
from app.services.email_service import EmailService


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def invitation_view(invitation):
    return {
        "id": str(invitation.id),
        "email": invitation.email,
        "delivery_status": invitation.delivery_status,
        "delivery_error": invitation.delivery_error,
        "created_at": invitation.created_at,
        "expires_at": invitation.expires_at,
        "sent_at": invitation.sent_at,
        "redeemed_at": invitation.redeemed_at,
        "revoked_at": invitation.revoked_at,
    }


def send_invitation(db: Session, submission_id, creator, previous_id=None):
    # Lock the shared submission, so create/resend cannot publish two current tokens.
    submission = db.execute(
        select(FormSubmission)
        .where(
            FormSubmission.id == submission_id,
            FormSubmission.form_type == "early_access",
        )
        .with_for_update()
    ).scalar_one_or_none()
    if submission is None:
        raise HTTPException(404, "Access request not found")
    email = submission.email.strip().lower()
    if len(email) > 255:
        raise HTTPException(422, "Invitation email exceeds account limit")
    existing_user = db.execute(
        select(User).where(func.lower(User.email) == email)
    ).scalar_one_or_none()
    if existing_user and (
        existing_user.is_verified or not existing_user.is_active or existing_user.role == Role.ADMIN
    ):
        raise HTTPException(409, "Account requires administrator review")
    history = (
        db.execute(
            select(AccessInvitation)
            .where(AccessInvitation.submission_id == submission.id)
            .order_by(AccessInvitation.created_at.desc(), AccessInvitation.id.desc())
            .with_for_update()
        )
        .scalars()
        .all()
    )
    now = datetime.now(UTC)
    if any(item.redeemed_at for item in history):
        raise HTTPException(409, "Access request already redeemed")
    if previous_id:
        if not any(item.id == previous_id for item in history):
            raise HTTPException(404, "Invitation not found")
    elif any(item.revoked_at is None for item in history):
        raise HTTPException(409, "Use resend to replace the existing invitation")
    for item in history:
        if item.revoked_at is None:
            item.revoked_at = now
    token = secrets.token_hex(32)
    invitation = AccessInvitation(
        submission_id=submission.id,
        email=email,
        token_hash=token_digest(token),
        expires_at=now + timedelta(hours=48),
        created_at=now,
        created_by=creator.id,
        delivery_status="pending",
    )
    db.add(invitation)
    db.commit()
    # Persist pending first. Never hold SQL locks across the provider call.
    accepted = EmailService.send_invitation_email(email, token)
    invitation.delivery_status = "sent" if accepted else "failed"
    invitation.delivery_error = None if accepted else "provider_not_accepted"
    invitation.sent_at = datetime.now(UTC) if accepted else None
    if not accepted:
        invitation.revoked_at = datetime.now(UTC)
    db.commit()
    db.refresh(invitation)
    return invitation, accepted


def redeem_invitation(db: Session, data):
    now = datetime.now(UTC)
    # Conditional UPDATE serializes competing claims even without SELECT FOR UPDATE.
    claimed = db.execute(
        update(AccessInvitation)
        .where(
            AccessInvitation.token_hash == token_digest(data.invitation_token),
            AccessInvitation.delivery_status == "sent",
            AccessInvitation.expires_at > now,
            AccessInvitation.revoked_at.is_(None),
            AccessInvitation.redeemed_at.is_(None),
        )
        .values(redeemed_at=now)
        .execution_options(synchronize_session=False)
        .returning(AccessInvitation.id)
    ).scalar_one_or_none()
    if claimed is None:
        db.rollback()
        raise HTTPException(400, "Invalid or expired invitation")
    try:
        invitation = db.get(AccessInvitation, claimed)
        user = db.execute(
            select(User).where(func.lower(User.email) == invitation.email).with_for_update(of=User)
        ).scalar_one_or_none()
        if user and (user.is_verified or not user.is_active or user.role == Role.ADMIN):
            db.rollback()
            raise HTTPException(409, "Account requires administrator review")
        if user is None:
            user = User(email=invitation.email, role=Role.OWNER, is_active=True)
            db.add(user)
        user.email = invitation.email
        user.name = data.name
        user.password_hash = get_password_hash(data.password)
        user.is_verified = True
        db.flush()
        invitation.redeemed_by = user.id
        # Old verification links remain implemented, but cannot revive this lifecycle.
        db.execute(
            update(EmailVerification)
            .where(
                EmailVerification.user_id == user.id,
                EmailVerification.used_at.is_(None),
            )
            .values(used_at=now)
        )
        db.commit()
        db.refresh(user)
        return user
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Account requires administrator review") from None
    except Exception:
        db.rollback()
        raise
