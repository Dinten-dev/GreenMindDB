"""Contact & Early Access form endpoints with email delivery and DB persistence."""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.auth import require_role
from app.database import get_db
from app.models.form_submission import FormSubmission
from app.models.user import Role, User
from app.rate_limit import limiter
from app.schemas.contact import ContactRequest, EarlyAccessRequest
from app.services.email_service import send_notification_email

logger = logging.getLogger(__name__)

router = APIRouter(tags=["contact"])


# ── Helpers ──────────────────────────────────────────


def _persist_submission(
    db: Session,
    form_type: str,
    name: str,
    email: str,
    company: str | None,
    country: str | None,
    message: str | None,
) -> None:
    """Write the form submission to the database."""
    submission = FormSubmission(
        form_type=form_type,
        name=name,
        email=email,
        company=company or None,
        country=country or None,
        message=message or None,
    )
    db.add(submission)
    db.commit()


# ── Public Endpoints ─────────────────────────────────


@router.post("/contact")
@limiter.limit("5/minute")
async def submit_contact(
    request: Request,
    payload: ContactRequest,
    db: Session = Depends(get_db),
):
    """Handle Contact form submission."""
    # Honeypot check
    if payload.website:
        return {"status": "ok"}

    # 1. Persist to database (durable backup)
    _persist_submission(
        db,
        form_type="contact",
        name=payload.name,
        email=payload.email,
        company=payload.company,
        country=None,
        message=payload.message,
    )

    # 2. Send notification email (best-effort)
    timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    body = (
        f"New Website Contact Request\n"
        f"{'=' * 40}\n\n"
        f"Name:    {payload.name}\n"
        f"Email:   {payload.email}\n"
        f"Company: {payload.company or '—'}\n\n"
        f"Message:\n{payload.message}\n\n"
        f"Submitted at: {timestamp}\n"
    )
    send_notification_email("New GreenMind Website Contact Request", body)

    return {"status": "ok"}


@router.post("/early-access")
@limiter.limit("5/minute")
async def submit_early_access(
    request: Request,
    payload: EarlyAccessRequest,
    db: Session = Depends(get_db),
):
    """Handle Early Access form submission."""
    # Honeypot check
    if payload.website:
        return {"status": "ok"}

    # 1. Persist to database (durable backup)
    _persist_submission(
        db,
        form_type="early_access",
        name=payload.name,
        email=payload.email,
        company=payload.company,
        country=payload.country,
        message=payload.message,
    )

    # 2. Send notification email (best-effort)
    timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    body = (
        f"New Early Access Request\n"
        f"{'=' * 40}\n\n"
        f"Name:    {payload.name}\n"
        f"Company: {payload.company}\n"
        f"Email:   {payload.email}\n"
        f"Country: {payload.country}\n\n"
        f"Message:\n{payload.message or '—'}\n\n"
        f"Submitted at: {timestamp}\n"
    )
    send_notification_email("New GreenMind Early Access Request", body)

    return {"status": "ok"}


# ── Admin Endpoint ───────────────────────────────────


@router.get("/submissions")
async def list_submissions(
    form_type: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([Role.ADMIN])),
):
    """List all form submissions (admin only). Optional filter by form_type."""
    query = db.query(FormSubmission).order_by(FormSubmission.created_at.desc())
    if form_type:
        query = query.filter(FormSubmission.form_type == form_type)

    rows = query.limit(200).all()
    return [
        {
            "id": str(row.id),
            "form_type": row.form_type,
            "name": row.name,
            "email": row.email,
            "company": row.company,
            "country": row.country,
            "message": row.message,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]
